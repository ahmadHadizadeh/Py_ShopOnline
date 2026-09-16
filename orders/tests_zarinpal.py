import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

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
