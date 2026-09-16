from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from django.conf import settings
from django.urls import reverse


@dataclass(frozen=True)
class GatewayInitiation:
    gateway_name: str
    transaction_id: str
    redirect_url: str


@dataclass(frozen=True)
class GatewayCallbackResult:
    transaction_id: str
    success: bool
    amount: Decimal
    reference_id: str | None = None
    raw_response: Mapping[str, Any] = field(default_factory=dict)


class GatewayVerificationError(ValueError):
    """Raised when a gateway callback cannot be trusted or normalized."""


class GatewayInitiationError(ValueError):
    """Raised when a gateway cannot create a payment request."""


class PaymentGateway(Protocol):
    name: str

    def initiate(self, *, payment) -> GatewayInitiation:
        ...

    def extract_transaction_id(self, *, data: Mapping[str, str]) -> str:
        ...

    def verify_callback(
        self,
        *,
        payment,
        data: Mapping[str, str],
    ) -> GatewayCallbackResult:
        ...


class PaymentGatewayRegistry:
    _gateways: dict[str, PaymentGateway] = {}

    @classmethod
    def register(cls, gateway: PaymentGateway) -> None:
        name = str(getattr(gateway, "name", "")).strip()
        if not name:
            raise ValueError("Payment gateway must define a non-empty name.")
        cls._gateways[name] = gateway

    @classmethod
    def get(cls, name: str) -> PaymentGateway:
        try:
            return cls._gateways[name]
        except KeyError as exc:
            raise LookupError(f"Payment gateway is not registered: {name}") from exc

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(cls._gateways.keys())


class MockGateway:
    name = "mock_gateway"

    def initiate(self, *, payment) -> GatewayInitiation:
        transaction_id = f"TRX-{uuid4().hex.upper()}"
        payment_gateway_url = reverse("orders:mock_payment_gateway")
        redirect_url = (
            f"{payment_gateway_url}"
            f"?trxid={transaction_id}"
            f"&order={payment.order.order_number}"
            f"&amount={payment.amount}"
        )
        return GatewayInitiation(
            gateway_name=self.name,
            transaction_id=transaction_id,
            redirect_url=redirect_url,
        )

    def extract_transaction_id(self, *, data: Mapping[str, str]) -> str:
        trxid = data.get("trxid")
        if not trxid:
            raise GatewayVerificationError("شناسه تراکنش نامعتبر است.")
        return str(trxid)

    def verify_callback(
        self,
        *,
        payment,
        data: Mapping[str, str],
    ) -> GatewayCallbackResult:
        trxid = self.extract_transaction_id(data=data)
        status = data.get("status")
        amount_value = data.get("amount")
        signature = data.get("signature")

        if not status:
            raise GatewayVerificationError("وضعیت تراکنش نامعتبر است.")
        if not amount_value:
            raise GatewayVerificationError("مبلغ تراکنش ارسال نشده است.")
        if not signature:
            raise GatewayVerificationError("امضای تراکنش ارسال نشده است.")

        try:
            amount = Decimal(str(amount_value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise GatewayVerificationError("مبلغ تراکنش نامعتبر است.") from exc

        callback_secret = getattr(
            settings,
            "PAYMENT_CALLBACK_SECRET",
            settings.SECRET_KEY,
        )
        payload = f"{trxid}:{amount}".encode("utf-8")
        expected_signature = hmac.new(
            callback_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            raise GatewayVerificationError("امضای تراکنش نامعتبر است.")

        raw_response = {
            str(key): str(value)
            for key, value in data.items()
            if str(key).lower()
            not in {"signature", "token", "api_key", "authorization"}
        }
        return GatewayCallbackResult(
            transaction_id=trxid,
            success=str(status).strip().lower() == "success",
            amount=amount,
            raw_response=raw_response,
        )


class ZarinPalGateway:
    name = "zarinpal"

    REQUEST_URL = "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
    VERIFY_URL = "https://sandbox.zarinpal.com/pg/v4/payment/verify.json"
    START_PAY_URL = "https://sandbox.zarinpal.com/pg/StartPay"

    def _setting(self, name: str, default: str = "") -> str:
        value = getattr(settings, name, None)
        if value is None:
            value = os.getenv(name, default)
        return str(value).strip()

    def _merchant_id(self) -> str:
        merchant_id = self._setting("ZARINPAL_MERCHANT_ID")
        if not merchant_id:
            raise GatewayInitiationError("ZarinPal merchant ID تنظیم نشده است.")
        return merchant_id

    def _callback_url(self) -> str:
        callback_url = self._setting("ZARINPAL_CALLBACK_URL")
        if not callback_url:
            raise GatewayInitiationError("ZarinPal callback URL تنظیم نشده است.")
        return callback_url

    def _request_url(self) -> str:
        return self._setting("ZARINPAL_REQUEST_URL", self.REQUEST_URL)

    def _verify_url(self) -> str:
        return self._setting("ZARINPAL_VERIFY_URL", self.VERIFY_URL)

    def _start_pay_url(self) -> str:
        return self._setting("ZARINPAL_STARTPAY_URL", self.START_PAY_URL).rstrip("/")

    def _currency(self) -> str:
        currency = self._setting("ZARINPAL_CURRENCY", "IRT").upper()
        if currency not in {"IRR", "IRT"}:
            raise GatewayInitiationError("ZarinPal currency باید IRR یا IRT باشد.")
        return currency

    def _timeout(self) -> float:
        raw = self._setting("ZARINPAL_HTTP_TIMEOUT", "15")
        try:
            timeout = float(raw)
        except (TypeError, ValueError) as exc:
            raise GatewayInitiationError("ZarinPal HTTP timeout نامعتبر است.") from exc
        if timeout <= 0:
            raise GatewayInitiationError("ZarinPal HTTP timeout باید مثبت باشد.")
        return timeout

    def _post_json(self, *, url: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout()) as response:
                response_body = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GatewayInitiationError("ارتباط با زرین‌پال برقرار نشد.") from exc

        try:
            decoded = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayInitiationError("پاسخ نامعتبر از زرین‌پال دریافت شد.") from exc

        if not isinstance(decoded, Mapping):
            raise GatewayInitiationError("ساختار پاسخ زرین‌پال نامعتبر است.")
        return decoded

    @staticmethod
    def _data(response: Mapping[str, Any]) -> Mapping[str, Any]:
        data = response.get("data")
        if isinstance(data, Mapping):
            return data
        raise GatewayInitiationError("داده پاسخ زرین‌پال موجود نیست.")

    @staticmethod
    def _code(data: Mapping[str, Any]) -> int:
        try:
            return int(data.get("code"))
        except (TypeError, ValueError) as exc:
            raise GatewayInitiationError("کد پاسخ زرین‌پال نامعتبر است.") from exc

    @staticmethod
    def _safe_response(data: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"code", "message", "authority", "ref_id", "fee", "fee_type"}
        return {str(key): value for key, value in data.items() if str(key) in allowed}

    def initiate(self, *, payment) -> GatewayInitiation:
        payload = {
            "merchant_id": self._merchant_id(),
            "currency": self._currency(),
            "amount": int(payment.amount),
            "callback_url": self._callback_url(),
            "description": f"Payment for order {payment.order.order_number}",
            "metadata": {"order_id": payment.order.order_number},
        }
        try:
            response = self._post_json(url=self._request_url(), payload=payload)
            data = self._data(response)
            code = self._code(data)
        except GatewayInitiationError:
            raise
        except Exception as exc:
            raise GatewayInitiationError("خطا در درخواست پرداخت زرین‌پال.") from exc

        if code != 100:
            message = str(data.get("message") or "درخواست پرداخت رد شد.")
            raise GatewayInitiationError(message)

        authority = str(data.get("authority") or "").strip()
        if not authority:
            raise GatewayInitiationError("Authority از زرین‌پال دریافت نشد.")

        return GatewayInitiation(
            gateway_name=self.name,
            transaction_id=authority,
            redirect_url=f"{self._start_pay_url()}/{authority}",
        )

    def extract_transaction_id(self, *, data: Mapping[str, str]) -> str:
        authority = data.get("Authority") or data.get("authority")
        if not authority:
            raise GatewayVerificationError("Authority از زرین‌پال دریافت نشد.")
        return str(authority).strip()

    def verify_callback(
        self,
        *,
        payment,
        data: Mapping[str, str],
    ) -> GatewayCallbackResult:
        authority = self.extract_transaction_id(data=data)
        status = str(data.get("Status") or data.get("status") or "").strip().upper()
        if not status:
            raise GatewayVerificationError("Status از زرین‌پال دریافت نشد.")

        if status != "OK":
            return GatewayCallbackResult(
                transaction_id=authority,
                success=False,
                amount=Decimal(payment.amount),
                raw_response={"status": status, "authority": authority},
            )

        payload = {
            "merchant_id": self._setting("ZARINPAL_MERCHANT_ID"),
            "amount": int(payment.amount),
            "authority": authority,
        }
        if not payload["merchant_id"]:
            raise GatewayVerificationError("ZarinPal merchant ID تنظیم نشده است.")

        try:
            response = self._post_json(url=self._verify_url(), payload=payload)
            data_response = self._data(response)
            code = self._code(data_response)
        except GatewayInitiationError as exc:
            raise GatewayVerificationError("خطا در تأیید پرداخت زرین‌پال.") from exc
        except Exception as exc:
            raise GatewayVerificationError("خطا در تأیید پرداخت زرین‌پال.") from exc

        success = code in {100, 101}
        reference_id = data_response.get("ref_id")
        safe_response = self._safe_response(data_response)
        safe_response["authority"] = authority

        return GatewayCallbackResult(
            transaction_id=authority,
            success=success,
            amount=Decimal(payment.amount),
            reference_id=str(reference_id) if reference_id is not None else None,
            raw_response=safe_response,
        )


PaymentGatewayRegistry.register(MockGateway())
PaymentGatewayRegistry.register(ZarinPalGateway())
