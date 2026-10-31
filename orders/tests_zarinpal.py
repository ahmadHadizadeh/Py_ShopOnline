import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from catalog.models.category import Category
from catalog.models.product import Product
from orders.models.order_item import OrderItem
from orders.models.orders import Order
from orders.models.payment import Payment

User = get_user_model()

from orders.payment.gateways import ZarinPalGateway


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


@override_settings(
    ZARINPAL_MERCHANT_ID="00000000-0000-0000-0000-000000000000",
    ZARINPAL_CALLBACK_URL="https://example.test/orders/payment/callback/zarinpal/",
    ZARINPAL_CURRENCY="IRT",
)
class ZarinPalGatewayTests(SimpleTestCase):
    def setUp(self):
        self.gateway = ZarinPalGateway()
        self.payment = SimpleNamespace(
            amount=Decimal("125000"),
            order=SimpleNamespace(order_number="ORD-1001"),
        )

    @patch("orders.payment.gateways.urlopen")
    def test_initiate_uses_documented_currency_and_metadata(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse(
            {"data": {"code": 100, "message": "Success", "authority": "A000001"}}
        )

        result = self.gateway.initiate(payment=self.payment)

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {
            "merchant_id": "00000000-0000-0000-0000-000000000000",
            "currency": "IRT",
            "amount": 125000,
            "callback_url": "https://example.test/orders/payment/callback/zarinpal/",
            "description": "Payment for order ORD-1001",
            "metadata": {"order_id": "ORD-1001"},
        })
        self.assertEqual(result.transaction_id, "A000001")
        self.assertEqual(
            result.redirect_url,
            "https://sandbox.zarinpal.com/pg/StartPay/A000001",
        )

    def test_callback_nok_does_not_claim_success(self):
        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000001", "Status": "NOK"},
        )

        self.assertFalse(result.success)
        self.assertEqual(result.transaction_id, "A000001")
        self.assertEqual(result.amount, Decimal("125000"))

    @patch("orders.payment.gateways.urlopen")
    def test_verify_uses_server_side_payment_amount_and_code_100(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse(
            {
                "data": {
                    "code": 100,
                    "message": "Verified",
                    "ref_id": 998877,
                    "fee_type": "Merchant",
                    "fee": 0,
                }
            }
        )

        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000001", "Status": "OK"},
        )

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {
            "merchant_id": "00000000-0000-0000-0000-000000000000",
            "amount": 125000,
            "authority": "A000001",
        })
        self.assertTrue(result.success)
        self.assertEqual(result.reference_id, "998877")
        self.assertNotIn("card_pan", result.raw_response)
        self.assertNotIn("card_hash", result.raw_response)

    @patch("orders.payment.gateways.urlopen")
    def test_verify_code_101_is_successful_repeat_verification(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse(
            {
                "data": {
                    "code": 101,
                    "message": "Already verified",
                    "ref_id": 998877,
                }
            }
        )

        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000001", "Status": "OK"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.reference_id, "998877")

    @patch("orders.payment.gateways.urlopen")
    def test_verify_non_success_code_is_failure(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse(
            {"data": {"code": -21, "message": "Failed"}}
        )

        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000001", "Status": "OK"},
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.reference_id)


    @override_settings(ZARINPAL_CURRENCY="IRR")
    @patch("orders.payment.gateways.urlopen")
    def test_initiate_converts_toman_to_rial_when_currency_is_irr(
        self, mocked_urlopen
    ):
        mocked_urlopen.return_value = _FakeResponse(
            {"data": {"code": 100, "message": "Success", "authority": "S000002"}}
        )

        self.gateway.initiate(payment=self.payment)

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["currency"], "IRR")
        self.assertEqual(payload["amount"], 1_250_000)

    @override_settings(ZARINPAL_CURRENCY="IRR")
    @patch("orders.payment.gateways.urlopen")
    def test_verify_uses_the_same_converted_amount_as_initiation(
        self, mocked_urlopen
    ):
        mocked_urlopen.return_value = _FakeResponse(
            {
                "data": {
                    "code": 100,
                    "message": "Verified",
                    "ref_id": 998878,
                }
            }
        )

        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "S000003", "Status": "OK"},
        )

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["amount"], 1_250_000)
        self.assertEqual(result.amount, Decimal("125000"))


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=["testserver"],
    PAYMENT_DEFAULT_GATEWAY="zarinpal",
    ZARINPAL_ENVIRONMENT="sandbox",
    ZARINPAL_MERCHANT_ID="00000000-0000-0000-0000-000000000000",
    ZARINPAL_CALLBACK_URL=(
        "https://shop.example.test/orders/payment/callback/zarinpal/"
    ),
    ZARINPAL_CURRENCY="IRT",
    ZARINPAL_REQUEST_URL=(
        "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
    ),
    ZARINPAL_VERIFY_URL=(
        "https://sandbox.zarinpal.com/pg/v4/payment/verify.json"
    ),
    ZARINPAL_STARTPAY_URL="https://sandbox.zarinpal.com/pg/StartPay",
)
class ZarinPalPaymentFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="zarinpal-flow-user",
            password="Test-password-123!",
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="ZarinPal Sandbox",
            slug="zarinpal-sandbox-test",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Sandbox payment product",
            slug="sandbox-payment-product",
            price=Decimal("125000"),
            stock=2,
            is_active=True,
            is_available_status=True,
        )
        self.order = Order.objects.create(
            user=self.user,
            status=Order.Status.PENDING,
            subtotal_amount=Decimal("125000"),
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
            unit_price=Decimal("125000"),
            subtotal_price=Decimal("125000"),
        )
        self.initiate_url = reverse(
            "orders:initiate_payment",
            kwargs={"order_number": self.order.order_number},
        )
        self.callback_url = reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": "zarinpal"},
        )

    @patch("orders.payment.gateways.urlopen")
    def test_sandbox_initiation_is_restricted_to_staff(self, mocked_urlopen):
        nonstaff = User.objects.create_user(
            username="zarinpal-public-user",
            password="Test-password-456!",
        )
        self.client.force_login(nonstaff)

        response = self.client.get(self.initiate_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("cart:detail"))
        self.assertFalse(Payment.objects.filter(order=self.order).exists())
        mocked_urlopen.assert_not_called()

    @patch("orders.payment.gateways.urlopen")
    def test_sandbox_request_callback_verification_and_idempotency(
        self, mocked_urlopen
    ):
        mocked_urlopen.side_effect = [
            _FakeResponse(
                {
                    "data": {
                        "code": 100,
                        "message": "Success",
                        "authority": "S000001",
                    }
                }
            ),
            _FakeResponse(
                {
                    "data": {
                        "code": 100,
                        "message": "Verified",
                        "ref_id": 987654,
                        "fee_type": "Merchant",
                        "fee": 0,
                    }
                }
            ),
        ]

        response = self.client.get(self.initiate_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "https://sandbox.zarinpal.com/pg/StartPay/S000001",
        )

        payment = Payment.objects.get(order=self.order)
        self.assertEqual(payment.gateway_name, "zarinpal")
        self.assertEqual(payment.transaction_code, "S000001")

        request_payload = json.loads(
            mocked_urlopen.call_args_list[0].args[0].data.decode("utf-8")
        )
        self.assertEqual(request_payload["currency"], "IRT")
        self.assertEqual(request_payload["amount"], 125000)
        self.assertEqual(
            request_payload["callback_url"],
            "https://shop.example.test/orders/payment/callback/zarinpal/",
        )

        callback = self.client.get(
            self.callback_url,
            {"Authority": "S000001", "Status": "OK"},
        )
        self.assertEqual(callback.status_code, 302)

        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(payment.reference_code, "987654")
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(mocked_urlopen.call_count, 2)

        repeated_callback = self.client.get(
            self.callback_url,
            {"Authority": "S000001", "Status": "OK"},
        )
        self.assertEqual(repeated_callback.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(mocked_urlopen.call_count, 2)

    @patch("orders.payment.gateways.urlopen")
    def test_sandbox_nok_callback_fails_payment_without_reducing_stock(
        self, mocked_urlopen
    ):
        mocked_urlopen.return_value = _FakeResponse(
            {
                "data": {
                    "code": 100,
                    "message": "Success",
                    "authority": "S000002",
                }
            }
        )

        response = self.client.get(self.initiate_url)
        self.assertEqual(response.status_code, 302)

        payment = Payment.objects.get(order=self.order)
        callback = self.client.get(
            self.callback_url,
            {"Authority": payment.transaction_code, "Status": "NOK"},
        )
        self.assertEqual(callback.status_code, 302)

        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(self.product.stock, 2)
        mocked_urlopen.assert_called_once()
