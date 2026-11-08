"""Real PostgreSQL concurrency and transaction tests for payment finalization."""

from decimal import Decimal
from types import SimpleNamespace
import threading
import time
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.db.models import Sum
from django.test import RequestFactory, TransactionTestCase
from unittest import skipUnless

from accounts.apps import STORE_MANAGER_GROUP_NAME
from accounts.models.profile import Profile
from cart.models import Cart, CartItem
from cart.services import add_product_to_cart
from catalog.models.category import Category
from catalog.models.product import Product
from catalog.models.variant import ProductVariant
from catalog.models.inventory import InventoryMovement
from catalog.services import adjust_product_stock
from orders.models.orders import Order
from orders.models.order_item import OrderItem
from orders.models.payment import Payment
from orders.payment.gateways import GatewayCallbackResult, PaymentGatewayRegistry
from orders.payment.services import PaymentService
from orders.services import OrderService


@skipUnless(
    connection.vendor == "postgresql",
    "These tests require real PostgreSQL row-level locking.",
)
class PostgreSQLPaymentConcurrencyTests(TransactionTestCase):
    """Exercise callback races using independent PostgreSQL connections."""

    reset_sequences = True

    def setUp(self):
        self.gateway_name = f"pgc_{uuid4().hex[:20]}"
        self.gateway = self.ConcurrentGateway(self.gateway_name)
        PaymentGatewayRegistry.register(self.gateway)

    class ConcurrentGateway:
        def __init__(self, name):
            self.name = name
            self.barrier = None
            self.verification_started = None
            self.allow_verification = None

        def extract_transaction_id(self, *, data):
            return str(data["transaction_id"])

        def verify_callback(self, *, payment, data):
            if self.barrier is not None:
                self.barrier.wait(timeout=10)
            if self.verification_started is not None:
                self.verification_started.set()
                if not self.allow_verification.wait(timeout=10):
                    raise RuntimeError("Callback verification release timed out.")
            return GatewayCallbackResult(
                transaction_id=payment.transaction_code,
                success=True,
                amount=payment.amount,
                reference_id=f"PG-CONCURRENCY-{payment.pk}",
                raw_response={"verified": True, "payment_id": payment.pk},
            )

        def build_redirect_url(self, *, payment, transaction_id):
            return "/"

        def initiate(self, *, payment):
            raise AssertionError("Initiation is not part of callback tests.")

    def _create_product(self, *, name, stock):
        category = Category.objects.create(
            name=f"Concurrency {name}",
            slug=f"concurrency-{name.lower()}-{uuid4().hex[:10]}",
        )
        return Product.objects.create(
            category=category,
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:10]}",
            price=Decimal("50000000"),
            stock=stock,
            is_active=True,
            is_available_status=stock > 0,
        )

    def _create_order_and_payment(self, *, products):
        user = get_user_model().objects.create_user(
            username=f"pg-concurrency-{uuid4().hex[:12]}"
        )
        # The user-created signal creates the profile; assert the fixture is complete.
        self.assertTrue(Profile.objects.filter(user=user).exists())

        cart = Cart.objects.create(user=user, status=Cart.STATUS_ORDERED)
        total = Decimal("0")
        order = Order.objects.create(
            user=user,
            cart=cart,
            subtotal_amount=Decimal("0"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("0"),
            status=Order.Status.PENDING,
        )

        for product, quantity in products:
            line_total = product.price * quantity
            total += line_total
            OrderItem.objects.create(
                order=order,
                product=product,
                variant_id=None,
                product_name=product.name,
                variant_name="",
                sku=None,
                quantity=quantity,
                unit_price=product.price,
                subtotal_price=line_total,
            )

        order.subtotal_amount = total
        order.final_amount = total
        order.save(update_fields=["subtotal_amount", "final_amount", "updated"])

        payment = Payment.objects.create(
            order=order,
            user=user,
            amount=total,
            status=Payment.Status.PENDING,
            transaction_code=f"PG-TRX-{uuid4().hex}",
            gateway_name=self.gateway_name,
        )
        return order, payment

    def _run_callbacks(self, transaction_codes):
        outcomes = []
        errors = []
        result_lock = threading.Lock()

        def worker(transaction_code):
            close_old_connections()
            try:
                # A broken lock-order implementation must fail promptly, not hang CI.
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '20s'")

                outcome = PaymentService.handle_callback(
                    gateway_name=self.gateway_name,
                    data={"transaction_id": transaction_code},
                )
                with result_lock:
                    outcomes.append(outcome)
            except BaseException as exc:
                with result_lock:
                    errors.append((transaction_code, repr(exc)))
            finally:
                connection.close()

        threads = [
            threading.Thread(
                target=worker,
                args=(transaction_code,),
                daemon=True,
            )
            for transaction_code in transaction_codes
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=25)

        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive, f"Callback workers did not finish: {alive}")
        self.assertFalse(errors, f"Concurrent callback errors: {errors}")
        return outcomes

    def test_stale_admin_product_save_does_not_restore_stock_after_payment_callback(self):
        product = self._create_product(name="Stale Admin Save Product", stock=3)
        order, payment = self._create_order_and_payment(products=[(product, 1)])

        # Simulate a Product instance loaded by the admin form before the
        # callback commits its inventory decrement.
        stale_product = Product.objects.get(pk=product.pk)
        stale_product.name = "Seller Updated Product Name"

        outcome = PaymentService.handle_callback(
            gateway_name=self.gateway_name,
            data={"transaction_id": payment.transaction_code},
        )
        self.assertTrue(outcome.success)

        admin_user = get_user_model().objects.create_superuser(
            username=f"stale-admin-{uuid4().hex[:10]}",
            email=f"stale-admin-{uuid4().hex[:10]}@example.test",
            password="TestPassword123!",
        )
        request = RequestFactory().post("/admin/catalog/product/")
        request.user = admin_user
        product_admin = admin.site._registry[Product]
        product_admin.save_model(
            request,
            stale_product,
            SimpleNamespace(changed_data=["name"]),
            change=True,
        )

        product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(product.name, "Seller Updated Product Name")
        self.assertEqual(product.stock, 2)
        self.assertTrue(product.is_available_status)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(order.stock_reduced)

    def test_manual_inventory_adjustment_races_payment_callback_without_lost_update(self):
        product = self._create_product(name="Manual Payment Race Product", stock=3)
        order, payment = self._create_order_and_payment(products=[(product, 1)])
        manager = get_user_model().objects.create_superuser(
            username=f"inventory-race-{uuid4().hex[:10]}",
            email=f"inventory-race-{uuid4().hex[:10]}@example.test",
            password="TestPassword123!",
        )
        self.gateway.barrier = threading.Barrier(2, timeout=10)
        manual_errors = []

        def manual_adjustment_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '20s'")
                worker_manager = get_user_model().objects.get(pk=manager.pk)
                # Synchronize the manual write with the callback's provider
                # verification boundary, then let both contend for Product.
                self.gateway.barrier.wait(timeout=10)
                adjust_product_stock(
                    product_id=product.pk,
                    delta=4,
                    reason="ورود هم‌زمان محموله جدید",
                    actor=worker_manager,
                )
            except BaseException as exc:
                manual_errors.append(repr(exc))
            finally:
                connection.close()

        manual_worker = threading.Thread(
            target=manual_adjustment_worker,
            daemon=True,
        )
        manual_worker.start()
        outcomes = self._run_callbacks([payment.transaction_code])
        manual_worker.join(timeout=25)

        self.assertFalse(manual_worker.is_alive(), "Manual inventory worker did not finish.")
        self.assertFalse(manual_errors, f"Manual inventory errors: {manual_errors}")
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].success)

        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(order.stock_reduced)
        self.assertEqual(product.stock, 6)

        movements = list(
            InventoryMovement.objects.filter(product=product).order_by("pk")
        )
        self.assertEqual(len(movements), 2)
        self.assertEqual(
            {movement.source for movement in movements},
            {
                InventoryMovement.Source.MANUAL_ADJUSTMENT,
                InventoryMovement.Source.ORDER_PAYMENT,
            },
        )
        self.assertEqual(sum(movement.delta for movement in movements), 3)
        for movement in movements:
            self.assertEqual(
                movement.quantity_after - movement.quantity_before,
                movement.delta,
            )

    def test_admin_cancellation_during_provider_verification_prevents_fulfillment(self):
        product = self._create_product(name="Cancel Callback Race Product", stock=3)
        order, payment = self._create_order_and_payment(products=[(product, 1)])
        self.gateway.verification_started = threading.Event()
        self.gateway.allow_verification = threading.Event()

        outcomes = []
        errors = []

        def callback_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '20s'")
                outcomes.append(
                    PaymentService.handle_callback(
                        gateway_name=self.gateway_name,
                        data={"transaction_id": payment.transaction_code},
                    )
                )
            except BaseException as exc:
                errors.append(repr(exc))
            finally:
                connection.close()

        worker = threading.Thread(target=callback_worker, daemon=True)
        worker.start()
        verification_started = self.gateway.verification_started.wait(timeout=10)

        try:
            self.assertTrue(
                verification_started,
                "The callback did not reach the external-verification boundary.",
            )
            # The provider call is blocked outside any DB transaction. Cancel the
            # unpaid order and commit before allowing callback finalization.
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.CANCELLED,
            )
        finally:
            self.gateway.allow_verification.set()

        worker.join(timeout=25)
        self.assertFalse(worker.is_alive(), "Callback worker did not finish.")
        self.assertFalse(errors, f"Callback worker failed: {errors}")
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].success)

        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(order.cancelled_at)
        self.assertIsNone(order.paid_at)
        self.assertFalse(order.stock_reduced)
        self.assertEqual(product.stock, 3)

    def test_duplicate_success_callbacks_reduce_stock_exactly_once(self):
        product = self._create_product(name="Duplicate Callback Product", stock=3)
        order, payment = self._create_order_and_payment(products=[(product, 1)])
        self.gateway.barrier = threading.Barrier(2, timeout=10)

        outcomes = self._run_callbacks(
            [payment.transaction_code, payment.transaction_code]
        )

        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(outcome.success for outcome in outcomes))

        payment.refresh_from_db()
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(order.stock_reduced)
        self.assertEqual(product.stock, 2)
        payment_movements = InventoryMovement.objects.filter(
            product=product,
            source=InventoryMovement.Source.ORDER_PAYMENT,
            order_number=order.order_number,
        )
        self.assertEqual(payment_movements.count(), 1)
        movement = payment_movements.get()
        self.assertEqual(movement.delta, -1)
        self.assertEqual(movement.quantity_before, 3)
        self.assertEqual(movement.quantity_after, 2)
        self.assertIsNone(movement.actor_id)

    def test_two_orders_cannot_oversell_the_last_shared_unit(self):
        product = self._create_product(name="Last Unit Product", stock=1)
        first_order, first_payment = self._create_order_and_payment(
            products=[(product, 1)]
        )
        second_order, second_payment = self._create_order_and_payment(
            products=[(product, 1)]
        )
        self.gateway.barrier = threading.Barrier(2, timeout=10)

        outcomes = self._run_callbacks(
            [first_payment.transaction_code, second_payment.transaction_code]
        )

        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(outcome.success for outcome in outcomes))

        first_order.refresh_from_db()
        second_order.refresh_from_db()
        first_payment.refresh_from_db()
        second_payment.refresh_from_db()
        product.refresh_from_db()

        self.assertEqual(product.stock, 0)
        self.assertGreaterEqual(product.stock, 0)
        self.assertEqual(
            sorted([first_order.status, second_order.status]),
            sorted([Order.Status.PAID, Order.Status.CANCELLED]),
        )
        self.assertEqual(
            sorted([first_payment.status, second_payment.status]),
            [Payment.Status.SUCCESS, Payment.Status.SUCCESS],
        )
        self.assertEqual(
            sum([first_order.stock_reduced, second_order.stock_reduced]),
            1,
        )
        cancelled_order = (
            first_order
            if first_order.status == Order.Status.CANCELLED
            else second_order
        )
        self.assertIn("بازپرداخت", cancelled_order.admin_note)

    def test_overlapping_multi_product_orders_complete_without_lock_timeout(self):
        first_product = self._create_product(
            name="Shared Product A",
            stock=2,
        )
        second_product = self._create_product(
            name="Shared Product B",
            stock=2,
        )
        first_order, first_payment = self._create_order_and_payment(
            products=[(first_product, 1), (second_product, 1)]
        )
        second_order, second_payment = self._create_order_and_payment(
            # Deliberately reverse the order-item insertion order.
            products=[(second_product, 1), (first_product, 1)]
        )
        self.gateway.barrier = threading.Barrier(2, timeout=10)

        outcomes = self._run_callbacks(
            [first_payment.transaction_code, second_payment.transaction_code]
        )

        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(outcome.success for outcome in outcomes))
        first_order.refresh_from_db()
        second_order.refresh_from_db()
        first_product.refresh_from_db()
        second_product.refresh_from_db()

        self.assertEqual(first_order.status, Order.Status.PAID)
        self.assertEqual(second_order.status, Order.Status.PAID)
        self.assertTrue(first_order.stock_reduced)
        self.assertTrue(second_order.stock_reduced)
        self.assertEqual(first_product.stock, 0)
        self.assertEqual(second_product.stock, 0)

    def test_inventory_validation_failure_does_not_partially_reduce_stock(self):
        available_product = self._create_product(
            name="Available Atomicity Product",
            stock=4,
        )
        unavailable_product = self._create_product(
            name="Unavailable Atomicity Product",
            stock=0,
        )
        order, payment = self._create_order_and_payment(
            products=[(available_product, 1), (unavailable_product, 1)]
        )

        outcome = PaymentService.handle_callback(
            gateway_name=self.gateway_name,
            data={"transaction_id": payment.transaction_code},
        )

        self.assertTrue(outcome.success)
        payment.refresh_from_db()
        order.refresh_from_db()
        available_product.refresh_from_db()
        unavailable_product.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertFalse(order.stock_reduced)
        self.assertEqual(available_product.stock, 4)
        self.assertEqual(unavailable_product.stock, 0)
        self.assertFalse(
            InventoryMovement.objects.filter(
                source=InventoryMovement.Source.ORDER_PAYMENT,
                order_number=order.order_number,
            ).exists()
        )
        self.assertIn("بازپرداخت", order.admin_note)


    def test_concurrent_cart_adds_cannot_exceed_shared_variant_stock(self):
        product = self._create_product(
            name="Concurrent Shared Cart Stock Product",
            stock=5,
        )
        red = ProductVariant.objects.create(
            product=product, name="color", value="red"
        )
        blue = ProductVariant.objects.create(
            product=product, name="color", value="blue"
        )
        user = get_user_model().objects.create_user(
            username=f"pg-cart-{uuid4().hex[:12]}"
        )
        cart = Cart.objects.create(user=user, status=Cart.STATUS_ACTIVE)
        barrier = threading.Barrier(2, timeout=10)
        outcomes = []
        errors = []
        result_lock = threading.Lock()

        def worker(variant_id):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '20s'")
                local_cart = Cart.objects.get(pk=cart.pk)
                local_product = Product.objects.get(pk=product.pk)
                variant = ProductVariant.objects.get(pk=variant_id)
                barrier.wait(timeout=10)
                try:
                    add_product_to_cart(
                        local_cart, local_product, quantity=3, variant=variant
                    )
                    outcome = "accepted"
                except ValidationError:
                    outcome = "rejected"
                with result_lock:
                    outcomes.append(outcome)
            except BaseException as exc:
                with result_lock:
                    errors.append(repr(exc))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(red.pk,), daemon=True),
            threading.Thread(target=worker, args=(blue.pk,), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=25)

        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive, f"Cart workers did not finish: {alive}")
        self.assertFalse(errors, f"Concurrent cart add errors: {errors}")
        self.assertEqual(sorted(outcomes), ["accepted", "rejected"])
        active_total = CartItem.objects.filter(
            cart=cart,
            status=CartItem.STATUS_ACTIVE,
        ).aggregate(total=Sum("quantity"))["total"]
        self.assertEqual(active_total, 3)

    def test_concurrent_manager_group_adds_cannot_create_two_managers(self):
        manager_group = Group.objects.get(name=STORE_MANAGER_GROUP_NAME)
        users = [
            get_user_model().objects.create_user(
                username=f"pg-manager-{uuid4().hex[:12]}"
            )
            for _ in range(2)
        ]
        barrier = threading.Barrier(2, timeout=10)
        outcomes = []
        errors = []
        result_lock = threading.Lock()

        def worker(user_id):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '20s'")
                user = get_user_model().objects.get(pk=user_id)
                group = Group.objects.get(pk=manager_group.pk)
                barrier.wait(timeout=10)
                try:
                    # RelatedManager.add() supplies the atomic block for m2m_changed.
                    user.groups.add(group)
                    outcome = "assigned"
                except ValidationError:
                    outcome = "rejected"
                with result_lock:
                    outcomes.append(outcome)
            except BaseException as exc:
                with result_lock:
                    errors.append(repr(exc))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(users[0].pk,), daemon=True),
            threading.Thread(target=worker, args=(users[1].pk,), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=25)

        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive, f"Manager workers did not finish: {alive}")
        self.assertFalse(errors, f"Concurrent manager assignment errors: {errors}")
        self.assertEqual(sorted(outcomes), ["assigned", "rejected"])
        self.assertEqual(
            get_user_model().objects.filter(
                groups=manager_group,
                is_superuser=False,
            ).count(),
            1,
        )


@skipUnless(
    connection.vendor == "postgresql",
    "These tests require real PostgreSQL row-level locking.",
)
class PostgreSQLVariantPriceLockingTests(TransactionTestCase):
    """Verify concurrent admin price edits serialize on the parent Product row."""

    reset_sequences = True

    def setUp(self):
        self.category = Category.objects.create(
            name="PostgreSQL Variant Price Category",
            slug=f"pg-variant-price-{uuid4().hex[:10]}",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="PostgreSQL Variant Price Product",
            slug=f"pg-variant-price-product-{uuid4().hex[:10]}",
            price=Decimal("100.00"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="color",
            value="red",
            price_adjustment=Decimal("-70.00"),
        )
        self.admin_user = get_user_model().objects.create_superuser(
            username=f"pg-variant-admin-{uuid4().hex[:10]}",
            email=f"pg-variant-admin-{uuid4().hex[:10]}@example.test",
            password="TestPassword123!",
        )

    def _product_form_data(self, product, *, price):
        return {
            "name": product.name,
            "name_en": product.name_en or "",
            "brand": product.brand or "",
            "slug": product.slug,
            "image": "",
            "description": product.description,
            "review": product.review,
            "price": str(price),
            "old_price": "",
            "stock": str(product.stock),
            "is_active": "on" if product.is_active else "",
            "highlights": product.highlights,
            "guarantee": product.guarantee,
            "delivery_info": product.delivery_info,
            "category": str(product.category_id),
            "related_products": [],
        }

    def _configure_worker_connection(self):
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SET statement_timeout = '20s'")
            cursor.execute("SELECT pg_backend_pid()")
            return cursor.fetchone()[0]

    def _wait_for_backend_lock(self, backend_pid, worker_finished, *, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT wait_event_type
                    FROM pg_stat_activity
                    WHERE pid = %s
                    """,
                    [backend_pid],
                )
                row = cursor.fetchone()

            if row is not None and row[0] == "Lock":
                return True
            if worker_finished.is_set():
                return False
            time.sleep(0.025)
        return False

    def _join_workers(self, *workers):
        for worker in workers:
            if worker.ident is not None:
                worker.join(timeout=25)
        alive = [worker.name for worker in workers if worker.is_alive()]
        self.assertFalse(alive, f"PostgreSQL price-edit workers did not finish: {alive}")

    def test_product_price_commit_blocks_conflicting_variant_adjustment(self):
        """The waiting Variant form must validate against the committed base price."""
        product_saved = threading.Event()
        release_product = threading.Event()
        variant_pid_ready = threading.Event()
        variant_finished = threading.Event()
        state = {}
        errors = []

        def product_worker():
            close_old_connections()
            try:
                self._configure_worker_connection()
                with transaction.atomic():
                    request = RequestFactory().post("/admin/catalog/product/")
                    request.user = get_user_model().objects.get(pk=self.admin_user.pk)
                    product = Product.objects.get(pk=self.product.pk)
                    product_admin = admin.site._registry[Product]
                    form_class = product_admin.get_form(request, product)
                    form = form_class(
                        data=self._product_form_data(product, price="80.00"),
                        instance=product,
                    )
                    if not form.is_valid():
                        state["product_errors"] = list(form.errors)
                        raise AssertionError(
                            f"Expected base-price update to validate: {form.errors}"
                        )
                    obj = form.save(commit=False)
                    product_admin.save_model(request, obj, form, change=True)
                    form.save_m2m()
                    state["product_saved"] = True
                    product_saved.set()
                    if not release_product.wait(timeout=15):
                        raise RuntimeError("Product transaction release timed out.")
            except BaseException as exc:
                errors.append(("product", repr(exc)))
            finally:
                product_saved.set()
                connection.close()

        def variant_worker():
            close_old_connections()
            try:
                backend_pid = self._configure_worker_connection()
                state["variant_backend_pid"] = backend_pid
                with transaction.atomic():
                    request = RequestFactory().post("/admin/catalog/productvariant/")
                    request.user = get_user_model().objects.get(pk=self.admin_user.pk)
                    variant = ProductVariant.objects.get(pk=self.variant.pk)
                    variant_admin = admin.site._registry[ProductVariant]
                    form_class = variant_admin.get_form(request, variant)
                    form = form_class(
                        data={
                            "product": str(self.product.pk),
                            "name": variant.name,
                            "value": variant.value,
                            "price_adjustment": "-90.00",
                        },
                        instance=variant,
                    )
                    state["variant_valid"] = form.is_valid()
                    state["variant_errors"] = list(form.errors)
                    if state["variant_valid"]:
                        obj = form.save(commit=False)
                        variant_admin.save_model(request, obj, form, change=True)
                        form.save_m2m()
            except BaseException as exc:
                errors.append(("variant", repr(exc)))
            finally:
                variant_finished.set()
                connection.close()

        product_thread = threading.Thread(target=product_worker, daemon=True)
        variant_thread = threading.Thread(target=variant_worker, daemon=True)
        lock_wait_observed = False

        try:
            product_thread.start()
            self.assertTrue(
                product_saved.wait(timeout=10),
                "Product admin transaction did not reach its saved/locked boundary.",
            )
            self.assertTrue(state.get("product_saved"), errors)

            variant_thread.start()
            deadline = time.monotonic() + 10
            while (
                "variant_backend_pid" not in state
                and not variant_finished.is_set()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertIn("variant_backend_pid", state, errors)
            lock_wait_observed = self._wait_for_backend_lock(
                state["variant_backend_pid"],
                variant_finished,
            )
        finally:
            release_product.set()
            self._join_workers(product_thread, variant_thread)

        self.assertFalse(errors, f"Concurrent admin price edits failed: {errors}")
        self.assertTrue(
            lock_wait_observed,
            "PostgreSQL did not report the Variant form waiting on the Product row lock.",
        )
        self.assertFalse(
            state["variant_valid"],
            "A conflicting Variant price adjustment was accepted after the base-price commit.",
        )
        self.assertIn("price_adjustment", state["variant_errors"])

        product = Product.objects.get(pk=self.product.pk)
        variant = ProductVariant.objects.get(pk=self.variant.pk)
        self.assertEqual(product.price, Decimal("80.00"))
        self.assertEqual(variant.price_adjustment, Decimal("-70.00"))
        self.assertGreaterEqual(product.price + variant.price_adjustment, Decimal("0"))

    def test_variant_adjustment_commit_blocks_conflicting_product_price_change(self):
        """The waiting Product form must recheck variants after the Variant commit."""
        variant_saved = threading.Event()
        release_variant = threading.Event()
        product_pid_ready = threading.Event()
        product_finished = threading.Event()
        state = {}
        errors = []

        def variant_worker():
            close_old_connections()
            try:
                self._configure_worker_connection()
                with transaction.atomic():
                    request = RequestFactory().post("/admin/catalog/productvariant/")
                    request.user = get_user_model().objects.get(pk=self.admin_user.pk)
                    variant = ProductVariant.objects.get(pk=self.variant.pk)
                    variant_admin = admin.site._registry[ProductVariant]
                    form_class = variant_admin.get_form(request, variant)
                    form = form_class(
                        data={
                            "product": str(self.product.pk),
                            "name": variant.name,
                            "value": variant.value,
                            "price_adjustment": "-90.00",
                        },
                        instance=variant,
                    )
                    if not form.is_valid():
                        state["variant_errors"] = list(form.errors)
                        raise AssertionError(
                            f"Expected Variant adjustment to validate: {form.errors}"
                        )
                    obj = form.save(commit=False)
                    variant_admin.save_model(request, obj, form, change=True)
                    form.save_m2m()
                    state["variant_saved"] = True
                    variant_saved.set()
                    if not release_variant.wait(timeout=15):
                        raise RuntimeError("Variant transaction release timed out.")
            except BaseException as exc:
                errors.append(("variant", repr(exc)))
            finally:
                variant_saved.set()
                connection.close()

        def product_worker():
            close_old_connections()
            try:
                backend_pid = self._configure_worker_connection()
                state["product_backend_pid"] = backend_pid
                with transaction.atomic():
                    request = RequestFactory().post("/admin/catalog/product/")
                    request.user = get_user_model().objects.get(pk=self.admin_user.pk)
                    product = Product.objects.get(pk=self.product.pk)
                    product_admin = admin.site._registry[Product]
                    form_class = product_admin.get_form(request, product)
                    form = form_class(
                        data=self._product_form_data(product, price="80.00"),
                        instance=product,
                    )
                    state["product_valid"] = form.is_valid()
                    state["product_errors"] = list(form.errors)
                    if state["product_valid"]:
                        obj = form.save(commit=False)
                        product_admin.save_model(request, obj, form, change=True)
                        form.save_m2m()
            except BaseException as exc:
                errors.append(("product", repr(exc)))
            finally:
                product_finished.set()
                connection.close()

        variant_thread = threading.Thread(target=variant_worker, daemon=True)
        product_thread = threading.Thread(target=product_worker, daemon=True)
        lock_wait_observed = False

        try:
            variant_thread.start()
            self.assertTrue(
                variant_saved.wait(timeout=10),
                "Variant admin transaction did not reach its saved/locked boundary.",
            )
            self.assertTrue(state.get("variant_saved"), errors)

            product_thread.start()
            deadline = time.monotonic() + 10
            while (
                "product_backend_pid" not in state
                and not product_finished.is_set()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertIn("product_backend_pid", state, errors)
            lock_wait_observed = self._wait_for_backend_lock(
                state["product_backend_pid"],
                product_finished,
            )
        finally:
            release_variant.set()
            self._join_workers(variant_thread, product_thread)

        self.assertFalse(errors, f"Concurrent admin price edits failed: {errors}")
        self.assertTrue(
            lock_wait_observed,
            "PostgreSQL did not report the Product form/save waiting on the Product row lock.",
        )
        self.assertFalse(
            state["product_valid"],
            "A conflicting Product base-price change was accepted after the Variant commit.",
        )
        self.assertIn("price", state["product_errors"])

        product = Product.objects.get(pk=self.product.pk)
        variant = ProductVariant.objects.get(pk=self.variant.pk)
        self.assertEqual(product.price, Decimal("100.00"))
        self.assertEqual(variant.price_adjustment, Decimal("-90.00"))
        self.assertGreaterEqual(product.price + variant.price_adjustment, Decimal("0"))
