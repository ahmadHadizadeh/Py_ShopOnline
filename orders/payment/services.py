from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from orders.models import Order, Payment
from .gateways import (
    GatewayInitiationError,
    GatewayVerificationError,
    PaymentGatewayRegistry,
)

DEFAULT_GATEWAY_NAME = str(
    getattr(settings, "PAYMENT_DEFAULT_GATEWAY", "mock_gateway")
).strip() or "mock_gateway"


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
    def initiate(*, user, order_number: str, gateway_name: str = DEFAULT_GATEWAY_NAME):
        if not user or not getattr(user, "is_authenticated", False):
            raise ValidationError("برای شروع پرداخت باید وارد حساب کاربری شوید.")

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

        if order.status != Order.Status.PENDING:
            order.status = Order.Status.PENDING
            order.save(update_fields=["status"])

        payment = (
            Payment.objects.select_for_update()
            .filter(order=order)
            .first()
        )

        if payment is None:
            payment = Payment.objects.create(
                order=order,
                user=user,
                amount=order.final_amount,
                status=Payment.Status.PENDING,
                gateway_name=gateway.name,
            )
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
    @transaction.atomic
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

        payment = (
            Payment.objects.select_related("order")
            .select_for_update()
            .filter(transaction_code=transaction_id)
            .first()
        )
        if payment is None:
            raise ValidationError("تراکنش پیدا نشد.")

        if payment.gateway_name != gateway.name:
            raise ValidationError("درگاه تراکنش با درگاه Callback مطابقت ندارد.")

        order = payment.order

        if payment.status == Payment.Status.SUCCESS:
            return PaymentCallbackOutcome(
                payment=payment,
                order=order,
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

        if result.amount != payment.amount:
            raise ValidationError("مبلغ تراکنش با سفارش مطابقت ندارد.")

        gateway_response = json.dumps(
            dict(result.raw_response),
            ensure_ascii=False,
            default=str,
        )

        reference_id = result.reference_id
        if result.success and not reference_id:
            reference_id = f"REF-{payment.pk}-{uuid4().hex[:12].upper()}"

        new_status = Payment.Status.SUCCESS if result.success else Payment.Status.FAILED
        payment.update_status_and_order(
            new_status=new_status,
            transaction_id=result.transaction_id,
            reference_id=reference_id,
            gateway_response=gateway_response,
        )

        return PaymentCallbackOutcome(
            payment=payment,
            order=order,
            success=(payment.status == Payment.Status.SUCCESS),
        )
