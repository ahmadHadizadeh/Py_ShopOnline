from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models.category import Category
from catalog.models.product import Product
from orders.models.orders import Order
from orders.models.order_item import OrderItem
from orders.models.payment import Payment
from orders.payment.gateways import build_mock_callback_signature


@override_settings(DEBUG=True, PAYMENT_DEFAULT_GATEWAY="mock_gateway")
class MockGatewayUIContractTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mock-gateway-ui-user",
            password="TestPassword123!",
        )
        self.client.force_login(self.user)
        self.category = Category.objects.create(
            name="Mock UI Category",
            slug="mock-ui-category",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Mock UI Product",
            slug="mock-ui-product",
            price=Decimal("10000"),
            stock=2,
            is_active=True,
            is_available_status=True,
        )
        self.order = Order.objects.create(
            user=self.user,
            status=Order.Status.PENDING,
            subtotal_amount=Decimal("10000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            variant_id=None,
            product_name=self.product.name,
            variant_name="",
            sku=getattr(self.product, "sku", None),
            quantity=1,
            unit_price=Decimal("10000"),
            subtotal_price=Decimal("10000"),
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user,
            amount=self.order.final_amount,
            status=Payment.Status.PENDING,
            gateway_name="mock_gateway",
            transaction_code="MOCK-UI-CONTRACT-001",
        )
        self.mock_url = reverse("orders:mock_payment_gateway")

    def test_signed_ui_callback_completes_matching_payment(self):
        response = self.client.get(
            self.mock_url,
            {
                "trxid": self.payment.transaction_code,
                "order": self.order.order_number,
                "amount": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        signature = response.context["signature"]
        amount = response.context["amount"]
        self.assertEqual(amount, self.payment.amount)
        self.assertEqual(
            signature,
            build_mock_callback_signature(
                transaction_id=self.payment.transaction_code,
                amount=self.payment.amount,
            ),
        )
        self.assertContains(response, 'name="signature"')

        callback = self.client.post(
            response.context["callback_url"],
            {
                "trxid": self.payment.transaction_code,
                "amount": str(amount),
                "status": "success",
                "signature": signature,
            },
        )
        self.assertEqual(callback.status_code, 302)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.product.stock, 1)

    @override_settings(DEBUG=False)
    def test_mock_page_and_callback_are_disabled_in_production(self):
        page = self.client.get(
            self.mock_url,
            {
                "trxid": self.payment.transaction_code,
                "order": self.order.order_number,
                "amount": str(self.payment.amount),
            },
        )
        callback = self.client.post(
            reverse(
                "orders:gateway_payment_callback",
                kwargs={"gateway_name": "mock_gateway"},
            ),
            {
                "trxid": self.payment.transaction_code,
                "amount": str(self.payment.amount),
                "status": "success",
                "signature": "invalid",
            },
        )
        self.assertEqual(page.status_code, 404)
        self.assertEqual(callback.status_code, 404)

    @override_settings(DEBUG=False, PAYMENT_DEFAULT_GATEWAY="mock_gateway")
    def test_production_does_not_create_mock_payment(self):
        self.payment.delete()
        response = self.client.get(
            reverse(
                "orders:initiate_payment",
                kwargs={"order_number": self.order.order_number},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("cart:detail"))
        self.assertFalse(Payment.objects.filter(order=self.order).exists())
