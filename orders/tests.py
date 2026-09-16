from decimal import Decimal
import hashlib
import hmac
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import Client, TestCase
from django.urls import reverse
from django.conf import settings
from django.core.exceptions import ValidationError
from accounts.models.profile import Profile
from accounts.signals import create_user_profile
from cart.models import Cart, CartItem
from catalog.models.category import Category
from catalog.models.product import Product
from orders.models.orders import Order
from orders.models.order_item import OrderItem
from orders.models.payment import Payment
from orders.payment.services import PaymentService

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

        self.callback_url = reverse("orders:payment_callback")

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
