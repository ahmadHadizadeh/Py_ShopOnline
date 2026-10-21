import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from orders.payment.gateways import (
    GatewayVerificationError,
    ZarinPalGateway,
)


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


@override_settings(
    ZARINPAL_MERCHANT_ID="test-merchant-id",
    ZARINPAL_CALLBACK_URL="https://shop.example/orders/payment/callback/zarinpal/",
    ZARINPAL_CURRENCY="IRT",
    ZARINPAL_REQUEST_URL="https://sandbox.zarinpal.com/pg/v4/payment/request.json",
    ZARINPAL_VERIFY_URL="https://sandbox.zarinpal.com/pg/v4/payment/verify.json",
    ZARINPAL_STARTPAY_URL="https://sandbox.zarinpal.com/pg/StartPay",
    ZARINPAL_HTTP_TIMEOUT="5",
)
class ZarinPalGatewayContractTests(SimpleTestCase):
    def setUp(self):
        self.gateway = ZarinPalGateway()
        self.payment = SimpleNamespace(
            amount=Decimal("50000000"),
            order=SimpleNamespace(order_number="ORD-TEST-001"),
        )

    @patch("orders.payment.gateways.urlopen")
    def test_request_uses_configured_currency_and_exact_stored_amount(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(
            {"data": {"code": 100, "authority": "A000000000000000000000000000001234"}}
        )

        result = self.gateway.initiate(payment=self.payment)

        self.assertEqual(result.transaction_id, "A000000000000000000000000000001234")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["amount"], 50000000)
        self.assertEqual(payload["currency"], "IRT")
        self.assertEqual(payload["merchant_id"], "test-merchant-id")
        self.assertEqual(payload["callback_url"], "https://shop.example/orders/payment/callback/zarinpal/")
        self.assertEqual(
            result.redirect_url,
            "https://sandbox.zarinpal.com/pg/StartPay/A000000000000000000000000000001234",
        )

    @patch("orders.payment.gateways.urlopen")
    def test_status_ok_requires_server_side_verify_and_persists_real_ref_id(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(
            {"data": {"code": 100, "ref_id": 987654321, "fee": 0, "fee_type": "Merchant"}}
        )

        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000000000000000000000000000001234", "Status": "OK"},
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(result.success)
        self.assertEqual(result.reference_id, "987654321")
        self.assertEqual(payload["merchant_id"], "test-merchant-id")
        self.assertEqual(payload["amount"], 50000000)
        self.assertEqual(
            payload["authority"],
            "A000000000000000000000000000001234",
        )
        self.assertNotIn("signature", payload)

    @patch("orders.payment.gateways.urlopen")
    def test_already_verified_code_101_requires_and_returns_real_ref_id(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(
            {"data": {"code": 101, "ref_id": 987654321}}
        )

        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000000000000000000000000000001234", "Status": "OK"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.reference_id, "987654321")

    @patch("orders.payment.gateways.urlopen")
    def test_success_code_without_real_ref_id_is_rejected(self, urlopen):
        urlopen.return_value = FakeHTTPResponse({"data": {"code": 100}})

        with self.assertRaises(GatewayVerificationError):
            self.gateway.verify_callback(
                payment=self.payment,
                data={"Authority": "A000000000000000000000000000001234", "Status": "OK"},
            )

    @patch("orders.payment.gateways.urlopen")
    def test_non_ok_callback_never_calls_verify_endpoint(self, urlopen):
        result = self.gateway.verify_callback(
            payment=self.payment,
            data={"Authority": "A000000000000000000000000000001234", "Status": "NOK"},
        )

        urlopen.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.amount, Decimal("50000000"))
        self.assertIsNone(result.reference_id)

    @patch("orders.payment.gateways.urlopen")
    def test_status_ok_without_authority_is_rejected_before_network(self, urlopen):
        with self.assertRaises(GatewayVerificationError):
            self.gateway.verify_callback(
                payment=self.payment,
                data={"Status": "OK"},
            )
        urlopen.assert_not_called()
