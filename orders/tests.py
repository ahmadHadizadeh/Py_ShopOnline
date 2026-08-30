from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models.profile import Profile
from accounts.signals import create_user_profile
from cart.models import Cart, CartItem
from catalog.models.category import Category
from catalog.models.product import Product
from orders.models.orders import Order
from orders.models.payment import Payment

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
            status=Cart.STATUS_ACTIVE,
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

        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user1,
            amount=Decimal("50000000"),
            status=Payment.Status.PENDING,
            transaction_code="TRX-TEST-VALID-001",
            gateway_name="mock_gateway",
        )

        self.callback_url = reverse("orders:payment_callback")

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
        self.assertEqual(self.payment.status, Payment.Status.PENDING)

    def test_callback_amount_mismatch_returns_bad_request(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "1000000",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)

    def test_callback_invalid_amount_format_returns_bad_request(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "invalid_number_format",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_callback_success_flow_and_cart_deletion(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
            },
        )
        expected_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )
        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertIsNotNone(self.payment.reference_code)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertFalse(CartItem.objects.filter(cart_id=self.cart.pk).exists())

    def test_callback_failed_flow_updates_payment_status(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "failed",
                "amount": "50000000",
            },
        )
        expected_url = reverse(
            "orders:payment_failed",
            kwargs={"order_number": self.order.order_number},
        )
        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_callback_idempotency_on_already_successful_payment(self):
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "REF-EXISTING-123"
        self.payment.save(update_fields=["status", "reference_code"])

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
            },
        )
        expected_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )
        self.assertRedirects(response, expected_url)
