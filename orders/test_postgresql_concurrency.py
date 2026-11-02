"""Real PostgreSQL concurrency and transaction tests for payment finalization."""

from decimal import Decimal
from types import SimpleNamespace
import threading
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import RequestFactory, TransactionTestCase
from unittest import skipUnless

from accounts.models.profile import Profile
from cart.models import Cart
from catalog.models.category import Category
from catalog.models.product import Product
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
        self.assertIn("بازپرداخت", order.admin_note)
