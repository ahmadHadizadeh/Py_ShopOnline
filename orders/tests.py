from decimal import Decimal
import hashlib
import hmac
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models.signals import post_save
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from accounts.models.profile import Profile
from accounts.signals import create_user_profile
from cart.models import Cart, CartItem
from catalog.models.category import Category
from catalog.models.product import Product
from orders.models.orders import Order
from orders.models.order_item import OrderItem
from orders.models.order_address_snapshot import OrderAddressSnapshot
from orders.models.payment import Payment
from orders.payment.services import PaymentService
from orders.services import OrderService

User = get_user_model()


class PaymentCallbackSecurityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_profile, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_profile, sender=User)
        super().tearDownClass()

    def setUp(self):
        self.user1 = User.objects.create_user(
            username="user_test_1",
            password="Password123!",
        )
        Profile.objects.create(
            user=self.user1,
            phone_number="09110000001",
        )

        self.user2 = User.objects.create_user(
            username="user_test_2",
            password="Password123!",
        )
        Profile.objects.create(
            user=self.user2,
            phone_number="09110000002",
        )

        self.client1 = Client()
        self.client1.force_login(self.user1)

        self.client2 = Client()
        self.client2.force_login(self.user2)

        self.category = Category.objects.create(
            name="لپ تاپ",
            slug="laptop",
        )

        self.product = Product.objects.create(
            category=self.category,
            name="Lenovo Legion 5",
            slug="lenovo-legion-5",
            price=Decimal("50000000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )

        self.cart = Cart.objects.create(
            user=self.user1,
            status=Cart.STATUS_ORDERED,
        )

        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            quantity=1,
            unit_price_snapshot=Decimal("50000000"),
            status=CartItem.STATUS_ACTIVE,
        )

        self.order = Order.objects.create(
            user=self.user1,
            cart=self.cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )

        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            variant_id=None,
            product_name=self.product.name,
            variant_name="",
            sku=getattr(self.product, "sku", None),
            quantity=1,
            unit_price=Decimal("50000000"),
            subtotal_price=Decimal("50000000"),
        )

        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user1,
            amount=Decimal("50000000"),
            status=Payment.Status.PENDING,
            transaction_code="TRX-TEST-VALID-001",
            gateway_name="mock_gateway",
        )

        self.callback_url = reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": "mock_gateway"},
        )

    def callback_signature(self):
        payload = (f"{self.payment.transaction_code}:" f"{self.payment.amount}").encode(
            "utf-8"
        )
        return hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

    def test_callback_ownership_forbidden_for_other_user(self):
        response = self.client2.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
            },
        )

        self.assertEqual(response.status_code, 400)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.PENDING,
        )

    def test_callback_amount_mismatch_returns_bad_request(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "1000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 400)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.PENDING,
        )

    def test_callback_invalid_amount_format_returns_bad_request(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "invalid_number_format",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 400)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.PENDING,
        )

    def test_callback_success_flow_preserves_cart(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        expected_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )

        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.cart.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertIsNotNone(
            self.payment.reference_code,
        )

        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

        self.assertTrue(
            CartItem.objects.filter(
                cart_id=self.cart.pk,
            ).exists()
        )

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        self.assertEqual(
            self.order.cart_id,
            self.cart.pk,
        )

    def test_callback_failed_flow_updates_payment_status(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "failed",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        expected_url = reverse(
            "orders:payment_failed",
            kwargs={"order_number": self.order.order_number},
        )

        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()
        self.cart.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.FAILED,
        )

        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        self.assertEqual(
            self.order.cart_id,
            self.cart.pk,
        )

    def test_failed_payment_keeps_order_retryable(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "failed",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_failed",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertIsNone(self.order.cancelled_at)

    def test_cancelled_order_cannot_be_paid(self):
        original_transaction_code = self.payment.transaction_code

        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            PaymentService.initiate(
                user=self.user1,
                order_number=self.order.order_number,
            )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(
            self.payment.transaction_code,
            original_transaction_code,
        )
        self.assertEqual(self.order.status, Order.Status.CANCELLED)


    def test_callback_idempotency_on_already_successful_payment(self):
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "REF-EXISTING-123"
        self.payment.save(
            update_fields=[
                "status",
                "reference_code",
            ]
        )

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        expected_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )

        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertEqual(
            self.payment.reference_code,
            "REF-EXISTING-123",
        )

        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_successful_payment_keeps_order_cart_relationship(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 302)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.cart.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertEqual(
            self.order.cart_id,
            self.cart.pk,
        )

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        self.assertTrue(
            CartItem.objects.filter(
                cart_id=self.cart.pk,
            ).exists()
        )

    def test_success_callback_does_not_change_payment_to_failed(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 302)

        self.payment.refresh_from_db()

        self.assertNotEqual(
            self.payment.status,
            Payment.Status.FAILED,
        )

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

    def test_successful_payment_reduces_stock_and_marks_order_paid(self):
        initial_stock = self.product.stock

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.product.refresh_from_db()
        self.order.refresh_from_db()
        self.payment.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            initial_stock - self.order_item.quantity,
        )

        self.assertTrue(self.order.stock_reduced)

        self.assertEqual(
            self.order.status,
            Order.Status.PAID,
        )

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertIsNotNone(self.order.paid_at)


@override_settings(PAYMENT_DEFAULT_GATEWAY="mock_gateway")
class PaymentServiceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_profile, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_profile, sender=User)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username="payment-service-user",
            password="Password123!",
        )
        Profile.objects.create(
            user=self.user,
            phone_number="09110000004",
        )

        self.category = Category.objects.create(
            name="Payment Service",
            slug="payment-service-test",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Payment Service Product",
            slug="payment-service-product",
            price=Decimal("50000000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )
        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ORDERED,
        )
        self.order = Order.objects.create(
            user=self.user,
            cart=self.cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )

        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            variant_id=None,
            product_name=self.product.name,
            variant_name="",
            sku=getattr(self.product, "sku", None),
            quantity=1,
            unit_price=Decimal("50000000"),
            subtotal_price=Decimal("50000000"),
        )

    def test_service_uses_registered_mock_gateway(self):
        payment, order, initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.assertIsNotNone(payment)
        self.assertEqual(order.pk, self.order.pk)
        self.assertEqual(payment.gateway_name, "mock_gateway")
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.transaction_code, initiation.transaction_id)
        self.assertIn("mock-payment-gateway", initiation.redirect_url)

    def test_failed_payment_can_be_retried_without_cancelling_order(self):
        payment, order, first_initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status"])

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

        retry_payment, retry_order, retry_initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.assertEqual(retry_payment.pk, payment.pk)
        self.assertEqual(retry_order.pk, order.pk)
        self.assertEqual(retry_payment.status, Payment.Status.PENDING)
        self.assertNotEqual(
            retry_initiation.transaction_id,
            first_initiation.transaction_id,
        )

    def test_service_reuses_existing_one_to_one_payment(self):
        first_payment, _, _ = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )
        first_pk = first_payment.pk

        second_payment, _, _ = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.assertEqual(second_payment.pk, first_pk)
        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )
        self.assertEqual(second_payment.status, Payment.Status.PENDING)

    def test_repeated_pending_initiation_reuses_existing_gateway_transaction(self):
        from orders.payment.gateways import PaymentGatewayRegistry

        first_payment, _, first_initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )
        gateway = PaymentGatewayRegistry.get("mock_gateway")

        with patch.object(gateway, "initiate", wraps=gateway.initiate) as mocked_initiate:
            second_payment, _, second_initiation = PaymentService.initiate(
                user=self.user,
                order_number=self.order.order_number,
            )

        self.assertEqual(first_payment.pk, second_payment.pk)
        self.assertEqual(first_initiation.transaction_id, second_initiation.transaction_id)
        self.assertEqual(first_initiation.redirect_url, second_initiation.redirect_url)
        mocked_initiate.assert_not_called()


class PaymentCallbackServiceTests(PaymentServiceTests):
    def setUp(self):
        super().setUp()
        self.payment, self.order, self.initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

    def test_service_handles_mock_callback(self):
        from orders.payment.gateways import PaymentGatewayRegistry
        gateway = PaymentGatewayRegistry.get("mock_gateway")
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        outcome = PaymentService.handle_callback(
            gateway_name="mock_gateway",
            data={
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": signature,
            },
        )

        self.assertTrue(outcome.success)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(gateway.name, self.payment.gateway_name)

    def test_service_callback_is_idempotent(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        data = {
            "trxid": self.payment.transaction_code,
            "status": "success",
            "amount": str(self.payment.amount),
            "signature": signature,
        }

        first = PaymentService.handle_callback(
            gateway_name="mock_gateway", data=data
        )
        self.payment.refresh_from_db()
        reference = self.payment.reference_code

        second = PaymentService.handle_callback(
            gateway_name="mock_gateway", data=data
        )
        self.payment.refresh_from_db()

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(reference, self.payment.reference_code)

    def test_service_rejects_wrong_gateway_for_payment(self):
        with self.assertRaises(ValidationError):
            PaymentService.handle_callback(
                gateway_name="unknown_gateway",
                data={
                    "trxid": self.payment.transaction_code,
                    "status": "success",
                    "amount": str(self.payment.amount),
                    "signature": "invalid",
                },
            )

    def test_repeated_success_callback_does_not_reduce_stock_twice(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        data = {
            "trxid": self.payment.transaction_code,
            "status": "success",
            "amount": str(self.payment.amount),
            "signature": signature,
        }

        initial_stock = self.product.stock
        PaymentService.handle_callback(gateway_name="mock_gateway", data=data)
        self.product.refresh_from_db()
        first_stock = self.product.stock

        PaymentService.handle_callback(gateway_name="mock_gateway", data=data)
        self.product.refresh_from_db()

        self.assertEqual(first_stock, initial_stock - 1)
        self.assertEqual(self.product.stock, first_stock)

    def test_callback_db_failure_rolls_back_payment_transition(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        data = {
            "trxid": self.payment.transaction_code,
            "status": "success",
            "amount": str(self.payment.amount),
            "signature": signature,
        }

        def mutate_then_fail(*args, **kwargs):
            self.payment.status = Payment.Status.SUCCESS
            self.payment.save(update_fields=["status"])
            raise RuntimeError("simulated payment transition failure")

        with patch.object(
            Payment,
            "update_status_and_order",
            side_effect=mutate_then_fail,
        ):
            with self.assertRaises(RuntimeError):
                PaymentService.handle_callback(
                    gateway_name="mock_gateway",
                    data=data,
                )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_callback_rejects_amount_drift_after_gateway_verification(self):
        from orders.payment.gateways import GatewayCallbackResult, PaymentGatewayRegistry

        gateway_name = "drift_test_gateway"
        transaction_id = "DRIFT-TEST-001"
        payment = self.payment
        payment.gateway_name = gateway_name
        payment.transaction_code = transaction_id
        payment.save(update_fields=["gateway_name", "transaction_code"])

        class DriftGateway:
            name = gateway_name

            def extract_transaction_id(self, *, data):
                return transaction_id

            def verify_callback(self, *, payment, data):
                # Simulate a concurrent order/payment change while provider
                # verification is in flight. Final DB validation must use the
                # freshly locked Payment row.
                Payment.objects.filter(pk=payment.pk).update(
                    amount=Decimal("60000000")
                )
                return GatewayCallbackResult(
                    transaction_id=transaction_id,
                    success=True,
                    amount=Decimal("50000000"),
                    reference_id="DRIFT-REF",
                    raw_response={"code": 100},
                )

            def build_redirect_url(self, *, payment, transaction_id):
                return "/"

            def initiate(self, *, payment):
                raise AssertionError("not used")

        PaymentGatewayRegistry.register(DriftGateway())

        with self.assertRaises(ValidationError):
            PaymentService.handle_callback(
                gateway_name=gateway_name,
                data={},
            )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount, Decimal("60000000"))


class PaymentCallbackTransactionBoundaryTests(TransactionTestCase):
    reset_sequences = True

    def test_gateway_verification_runs_outside_database_transaction(self):
        from orders.payment.gateways import GatewayCallbackResult, PaymentGatewayRegistry

        user = User.objects.create_user(username="boundary-user")
        profile = Profile.objects.get(user=user)
        profile.phone_number = "09110000011"
        profile.save(update_fields=["phone_number"])
        category = Category.objects.create(name="Boundary", slug="boundary")
        product = Product.objects.create(
            category=category,
            name="Boundary Product",
            slug="boundary-product",
            price=Decimal("50000000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )
        cart = Cart.objects.create(user=user, status=Cart.STATUS_ORDERED)
        order = Order.objects.create(
            user=user,
            cart=cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            variant_id=None,
            product_name=product.name,
            variant_name="",
            sku=getattr(product, "sku", None),
            quantity=1,
            unit_price=Decimal("50000000"),
            subtotal_price=Decimal("50000000"),
        )
        payment = Payment.objects.create(
            order=order,
            user=user,
            amount=Decimal("50000000"),
            status=Payment.Status.PENDING,
            transaction_code="BOUNDARY-001",
            gateway_name="boundary_test_gateway",
        )

        observed_atomic_state = {"value": None}

        class BoundaryGateway:
            name = "boundary_test_gateway"

            def extract_transaction_id(self, *, data):
                return "BOUNDARY-001"

            def verify_callback(self, *, payment, data):
                observed_atomic_state["value"] = connection.in_atomic_block
                return GatewayCallbackResult(
                    transaction_id="BOUNDARY-001",
                    success=False,
                    amount=Decimal("50000000"),
                    raw_response={"status": "NOK"},
                )

            def build_redirect_url(self, *, payment, transaction_id):
                return "/"

            def initiate(self, *, payment):
                raise AssertionError("not used")

        PaymentGatewayRegistry.register(BoundaryGateway())

        PaymentService.handle_callback(
            gateway_name="boundary_test_gateway",
            data={"Authority": "BOUNDARY-001", "Status": "NOK"},
        )

        self.assertFalse(observed_atomic_state["value"])


@override_settings(PAYMENT_DEFAULT_GATEWAY="mock_gateway")
class ProcessPaymentViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_profile, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_profile, sender=User)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username="payment-process-user",
            password="Password123!",
        )

        Profile.objects.create(
            user=self.user,
            phone_number="09110000003",
        )

        self.client = Client()
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="پرداخت",
            slug="payment-test",
        )

        self.product = Product.objects.create(
            category=self.category,
            name="Payment Test Product",
            slug="payment-test-product",
            price=Decimal("50000000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )

        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ORDERED,
        )

        self.order = Order.objects.create(
            user=self.user,
            cart=self.cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )

        self.url = reverse(
            "orders:initiate_payment",
            kwargs={"order_number": self.order.order_number},
        )

    def test_initiate_payment_creates_single_pending_payment(self):
        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            302,
        )

        payment = Payment.objects.get(order=self.order)

        expected_url = (
            reverse("orders:mock_payment_gateway")
            + f"?trxid={payment.transaction_code}"
            + f"&order={self.order.order_number}"
            + "&amount=50000000"
        )

        self.assertEqual(
            response.url,
            expected_url,
        )

        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )

        self.assertEqual(
            payment.status,
            Payment.Status.PENDING,
        )

        self.assertEqual(
            payment.amount,
            Decimal("50000000"),
        )

        self.assertEqual(
            payment.gateway_name,
            "mock_gateway",
        )

    def test_repeated_initiate_payment_reuses_existing_payment(self):
        first_response = self.client.get(self.url)

        self.assertEqual(
            first_response.status_code,
            302,
        )

        first_payment = Payment.objects.get(order=self.order)

        second_response = self.client.get(self.url)

        self.assertEqual(
            second_response.status_code,
            302,
        )

        second_payment = Payment.objects.get(order=self.order)

        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )

        self.assertEqual(
            second_payment.pk,
            first_payment.pk,
        )

        self.assertEqual(
            second_payment.status,
            Payment.Status.PENDING,
        )

    def test_paid_order_cannot_start_new_payment(self):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.assertFalse(Payment.objects.filter(order=self.order).exists())


class AdminOperationalTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-ops-user",
            email="admin-ops@example.com",
            password="AdminPass123!",
        )
        self.client.force_login(self.admin_user)

        self.category = Category.objects.create(
            name="Admin Ops Category",
            slug="admin-ops-category",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Admin Ops Product",
            slug="admin-ops-product",
            price=Decimal("1200000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )
        self.order = Order.objects.create(
            user=self.admin_user,
            subtotal_amount=Decimal("1200000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("150000"),
            final_amount=Decimal("1350000"),
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("1200000"),
            subtotal_price=Decimal("1200000"),
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.admin_user,
            amount=Decimal("1350000"),
            status=Payment.Status.PENDING,
            gateway_name="mock_gateway",
            transaction_code="ADMIN-TRX-001",
        )

    def test_operational_models_are_registered_in_admin(self):
        for model in (Order, OrderItem, Payment):
            self.assertIn(model, admin.site._registry)

        order_admin = admin.site._registry[Order]
        payment_admin = admin.site._registry[Payment]

        self.assertIn("status", order_admin.readonly_fields)
        self.assertIn("final_amount", order_admin.readonly_fields)
        self.assertIn("stock_reduced", order_admin.readonly_fields)
        self.assertIn("status", payment_admin.readonly_fields)
        self.assertIn("transaction_code", payment_admin.readonly_fields)

    def test_order_admin_changelist_is_operational(self):
        response = self.client.get(
            reverse("admin:orders_order_changelist")
        )

        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn(self.order.order_number, content)
        self.assertIn("در انتظار پرداخت", content)

    def test_payment_admin_changelist_is_operational(self):
        response = self.client.get(
            reverse("admin:orders_payment_changelist")
        )

        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn(self.order.order_number, content)
        self.assertIn(self.payment.transaction_code, content)


class OrderStatusTransitionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="order-status-user",
            password="Password123!",
        )
        self.category = Category.objects.create(
            name="Order Status Category",
            slug="order-status-category",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Order Status Product",
            slug="order-status-product",
            price=Decimal("1000000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )

    def create_order(self, status):
        order = Order.objects.create(
            user=self.user,
            subtotal_amount=Decimal("1000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("1000000"),
            status=status,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("1000000"),
            subtotal_price=Decimal("1000000"),
        )
        return order

    def test_paid_order_can_move_to_processing(self):
        order = self.create_order(Order.Status.PAID)
        result = OrderService.transition_status(
            order_id=order.pk,
            new_status=Order.Status.PROCESSING,
        )
        self.assertEqual(result.status, Order.Status.PROCESSING)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)

    def test_processing_order_can_move_to_completed_and_sets_timestamp(self):
        order = self.create_order(Order.Status.PROCESSING)
        result = OrderService.transition_status(
            order_id=order.pk,
            new_status=Order.Status.COMPLETED,
        )
        self.assertEqual(result.status, Order.Status.COMPLETED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)

    def test_unpaid_order_can_be_cancelled_and_sets_timestamp(self):
        order = self.create_order(Order.Status.PENDING)
        result = OrderService.transition_status(
            order_id=order.pk,
            new_status=Order.Status.CANCELLED,
        )
        self.assertEqual(result.status, Order.Status.CANCELLED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(order.cancelled_at)

    def test_invalid_status_transition_is_rejected(self):
        order = self.create_order(Order.Status.PAID)
        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.COMPLETED,
            )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)


class AdminOrderWorkflowConfigurationTests(TestCase):
    def test_order_admin_exposes_only_service_backed_workflow_actions(self):
        order_admin = admin.site._registry[Order]
        action_names = {action.__name__ for action in order_admin.actions}
        self.assertEqual(
            action_names,
            {
                "move_paid_orders_to_processing",
                "move_processing_orders_to_completed",
                "cancel_unpaid_orders",
            },
        )


class PaymentPresentationContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="payment-presentation-user",
            password="Password123!",
        )
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="Payment Presentation",
            slug="payment-presentation",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Payment Presentation Product",
            slug="payment-presentation-product",
            price=Decimal("125000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )

        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ORDERED,
        )
        self.order = Order.objects.create(
            user=self.user,
            cart=self.cart,
            subtotal_amount=Decimal("125000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("15000"),
            final_amount=Decimal("140000"),
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("125000"),
            subtotal_price=Decimal("125000"),
        )
        OrderAddressSnapshot.objects.create(
            order=self.order,
            recipient_name="کاربر تست",
            recipient_mobile="09120000000",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="آدرس تست",
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user,
            amount=Decimal("140000"),
            status=Payment.Status.PENDING,
            gateway_name="zarinpal",
            transaction_code="AUTH-PRESENT-001",
        )

    def test_confirmation_pending_state_renders_correct_payment_action(self):
        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("پیش‌فاکتور نهایی سفارش", content)
        self.assertIn("در انتظار پرداخت", content)
        self.assertIn("پرداخت آنلاین و نهایی", content)
        self.assertIn(self.order.order_number, content)
        self.assertIn("تاریخ ثبت:", content)

    def test_confirmation_failed_state_exposes_retry_action(self):
        self.payment.status = Payment.Status.FAILED
        self.payment.save(update_fields=["status"])

        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("پرداخت قبلی ناموفق بوده است", content)
        self.assertIn("تلاش مجدد برای پرداخت", content)
        self.assertIn(self.payment.transaction_code, content)

    def test_confirmation_paid_order_redirects_to_success_page(self):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])

        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )

    def test_success_receipt_distinguishes_reference_and_transaction(self):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "REF-PRESENT-001"
        self.payment.save(update_fields=["status", "reference_code"])

        response = self.client.get(
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("کد رهگیری پرداخت:", content)
        self.assertIn(self.payment.reference_code, content)
        self.assertIn("شناسه تراکنش درگاه:", content)
        self.assertIn(self.payment.transaction_code, content)
        self.assertNotIn("کد پیگیری تراکنش:", content)

    def test_failed_receipt_labels_transaction_code_correctly(self):
        self.payment.status = Payment.Status.FAILED
        self.payment.reference_code = ""
        self.payment.save(update_fields=["status", "reference_code"])

        response = self.client.get(
            reverse(
                "orders:payment_failed",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("شناسه تراکنش درگاه:", content)
        self.assertIn(self.payment.transaction_code, content)
        self.assertNotIn("کد رهگیری تراکنش:", content)
