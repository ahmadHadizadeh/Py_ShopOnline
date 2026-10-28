from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from orders.models import Order, Payment
from .gateways import (
    GatewayInitiation,
    GatewayInitiationError,
    GatewayVerificationError,
    PaymentGatewayRegistry,
)

def get_default_gateway_name() -> str:
    value = getattr(settings, "PAYMENT_DEFAULT_GATEWAY", "mock_gateway")
    return str(value).strip() or "mock_gateway"


def _quarantine_inconsistent_successful_payment_order(order) -> bool:
    """Prevent fulfilment when a recorded successful payment has a payable order state."""
    if order.status not in {Order.Status.PENDING, Order.Status.PLACED}:
        return False

    order.status = Order.Status.CANCELLED
    order.cancelled_at = timezone.now()
    reconciliation_note = (
        "پرداخت این سفارش قبلاً موفق ثبت شده، اما وضعیت سفارش با پرداخت "
        "سازگار نیست. سفارش برای جلوگیری از ارسال خودکار لغو شد؛ "
        "بررسی مالی و موجودی/بازپرداخت لازم است."
    )
    existing_note = (order.admin_note or "").strip()
    order.admin_note = "\n\n".join(
        note for note in (existing_note, reconciliation_note) if note
    )
    order.save(
        update_fields=["status", "cancelled_at", "admin_note", "updated"]
    )
    return True


@dataclass(frozen=True)
class PaymentCallbackOutcome:
    payment: Payment
    order: Order
    success: bool


class PaymentService:
    """Payment orchestration layer.

    Provider-specific behavior belongs to gateway adapters; views remain HTTP-only.
    """

    @staticmethod
    @transaction.atomic
    def initiate(*, user, order_number: str, gateway_name: str | None = None):
        if not user or not getattr(user, "is_authenticated", False):
            raise ValidationError("برای شروع پرداخت باید وارد حساب کاربری شوید.")

        gateway_name = gateway_name or get_default_gateway_name()

        try:
            gateway = PaymentGatewayRegistry.get(gateway_name)
        except LookupError as exc:
            raise ValidationError(f"درگاه پرداخت ثبت نشده است: {gateway_name}") from exc

        order = get_object_or_404(
            Order.objects.select_for_update(),
            order_number=order_number,
            user=user,
        )

        non_payable_statuses = {
            Order.Status.PAID,
            Order.Status.PROCESSING,
            Order.Status.COMPLETED,
        }
        if order.status in non_payable_statuses:
            return None, order, None

        if order.status == Order.Status.CANCELLED:
            raise ValidationError(
                "این سفارش لغو شده و امکان پرداخت مجدد برای آن وجود ندارد."
            )

        if order.status not in {
            Order.Status.PENDING,
            Order.Status.PLACED,
        }:
            raise ValidationError(
                "وضعیت فعلی سفارش برای شروع پرداخت معتبر نیست."
            )

        if order.status != Order.Status.PENDING:
            order.status = Order.Status.PENDING
            order.save(update_fields=["status", "updated"])

        payment = (
            Payment.objects.select_for_update()
            .filter(order=order)
            .first()
        )

        if payment is not None and payment.status == Payment.Status.SUCCESS:
            # Never charge again when a successful payment has an inconsistent order state.
            _quarantine_inconsistent_successful_payment_order(order)
            return None, order, None

        if payment is None:
            payment = Payment.objects.create(
                order=order,
                user=user,
                amount=order.final_amount,
                status=Payment.Status.PENDING,
                gateway_name=gateway.name,
            )
        elif (
            payment.status == Payment.Status.PENDING
            and payment.gateway_name == gateway.name
            and payment.transaction_code
        ):
            # Reuse the live gateway transaction. This prevents repeated clicks
            # from requesting a second authority/transaction for the same order.
            initiation = GatewayInitiation(
                gateway_name=gateway.name,
                transaction_id=payment.transaction_code,
                redirect_url=gateway.build_redirect_url(
                    payment=payment,
                    transaction_id=payment.transaction_code,
                ),
            )
            return payment, order, initiation
        else:
            payment.status = Payment.Status.PENDING
            payment.amount = order.final_amount
            payment.gateway_name = gateway.name
            payment.save(
                update_fields=[
                    "status",
                    "amount",
                    "gateway_name",
                ]
            )

        try:
            initiation = gateway.initiate(payment=payment)
        except GatewayInitiationError as exc:
            raise ValidationError(str(exc)) from exc

        payment.transaction_code = initiation.transaction_id
        payment.save(update_fields=["transaction_code", "updated"])

        return payment, order, initiation

    @staticmethod
    def handle_callback(*, gateway_name: str, data) -> PaymentCallbackOutcome:
        try:
            gateway = PaymentGatewayRegistry.get(gateway_name)
        except LookupError as exc:
            raise ValidationError(f"درگاه پرداخت ثبت نشده است: {gateway_name}") from exc

        try:
            transaction_id = gateway.extract_transaction_id(data=data)
        except GatewayVerificationError:
            raise
        except Exception as exc:
            raise GatewayVerificationError("شناسه تراکنش در callback نامعتبر است.") from exc

        # Read the candidate payment before calling the remote gateway. The
        # provider verification is intentionally outside the DB transaction so a
        # network round-trip does not hold row locks for the duration of the call.
        payment = (
            Payment.objects
            .select_related("order")
            .filter(transaction_code=transaction_id)
            .first()
        )
        if payment is None:
            raise ValidationError("تراکنش پیدا نشد.")

        if payment.gateway_name != gateway.name:
            raise ValidationError("درگاه تراکنش با درگاه Callback مطابقت ندارد.")

        if payment.status == Payment.Status.SUCCESS:
            if payment.order.status in {
                Order.Status.PENDING,
                Order.Status.PLACED,
            }:
                # A corrupt order/payment pair may need reconciliation, but do not
                # mutate it based on an unauthenticated duplicate callback.
                try:
                    result = gateway.verify_callback(payment=payment, data=data)
                except GatewayVerificationError:
                    raise
                except Exception as exc:
                    raise GatewayVerificationError(
                        "خطا در اعتبارسنجی پاسخ درگاه."
                    ) from exc

                if not result.success:
                    return PaymentCallbackOutcome(
                        payment=payment,
                        order=payment.order,
                        success=True,
                    )
                if result.transaction_id != payment.transaction_code:
                    raise ValidationError("شناسه تراکنش با رکورد پرداخت مطابقت ندارد.")
                if result.amount != payment.amount:
                    raise ValidationError("مبلغ تراکنش با سفارش مطابقت ندارد.")

                with transaction.atomic():
                    locked_payment = (
                        Payment.objects.select_related("order")
                        .select_for_update()
                        .get(pk=payment.pk)
                    )
                    locked_order = locked_payment.order
                    if locked_payment.status == Payment.Status.SUCCESS:
                        _quarantine_inconsistent_successful_payment_order(locked_order)
                        return PaymentCallbackOutcome(
                            payment=locked_payment,
                            order=locked_order,
                            success=True,
                        )
                payment = Payment.objects.select_related("order").get(pk=payment.pk)
            else:
                return PaymentCallbackOutcome(
                    payment=payment,
                    order=payment.order,
                    success=True,
                )

        try:
            result = gateway.verify_callback(
                payment=payment,
                data=data,
            )
        except GatewayVerificationError:
            raise
        except Exception as exc:
            raise GatewayVerificationError("خطا در اعتبارسنجی پاسخ درگاه.") from exc

        with transaction.atomic():
            locked_payment = (
                Payment.objects
                .select_related("order")
                .select_for_update()
                .get(pk=payment.pk)
            )
            locked_order = locked_payment.order

            if locked_payment.gateway_name != gateway.name:
                raise ValidationError("درگاه تراکنش با درگاه Callback مطابقت ندارد.")

            # A concurrent callback may have committed SUCCESS while the gateway
            # verification was in flight. Treat that as the idempotent outcome.
            if locked_payment.status == Payment.Status.SUCCESS:
                _quarantine_inconsistent_successful_payment_order(locked_order)
                return PaymentCallbackOutcome(
                    payment=locked_payment,
                    order=locked_order,
                    success=True,
                )

            if locked_payment.transaction_code != result.transaction_id:
                raise ValidationError("شناسه تراکنش با رکورد پرداخت مطابقت ندارد.")

            if result.amount != locked_payment.amount:
                raise ValidationError("مبلغ تراکنش با سفارش مطابقت ندارد.")

            gateway_response = json.dumps(
                dict(result.raw_response),
                ensure_ascii=False,
                default=str,
            )

            reference_id = result.reference_id
            if reference_id is not None:
                reference_id = str(reference_id).strip() or None

            if result.success and not reference_id:
                if gateway.name == "zarinpal":
                    raise GatewayVerificationError(
                        "زرین‌پال پرداخت موفق را بدون شناسه مرجع معتبر برگرداند."
                    )
                reference_id = (
                    f"REF-{locked_payment.pk}-{uuid4().hex[:12].upper()}"
                )

            new_status = (
                Payment.Status.SUCCESS
                if result.success
                else Payment.Status.FAILED
            )
            locked_payment.update_status_and_order(
                new_status=new_status,
                transaction_id=result.transaction_id,
                reference_id=reference_id,
                gateway_response=gateway_response,
            )

            return PaymentCallbackOutcome(
                payment=locked_payment,
                order=locked_order,
                success=(locked_payment.status == Payment.Status.SUCCESS),
            )

