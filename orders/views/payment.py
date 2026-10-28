import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from orders.models.orders import Order
from orders.models.payment import Payment
from orders.payment.gateways import (
    GatewayVerificationError,
    build_mock_callback_signature,
)
from orders.payment.services import (
    PaymentReconciliationRequired,
    PaymentService,
    get_default_gateway_name,
)

logger = logging.getLogger(__name__)


class ProcessPaymentView(LoginRequiredMixin, View):
    login_url = "/accounts/login/"

    def get(self, request, order_number):
        return self._start_payment(request, order_number)

    def post(self, request, order_number):
        return self._start_payment(request, order_number)

    def _start_payment(self, request, order_number):
        if (
            not settings.DEBUG
            and get_default_gateway_name() == "zarinpal"
            and getattr(settings, "ZARINPAL_ENVIRONMENT", "production") == "sandbox"
            and not request.user.is_staff
        ):
            messages.error(
                request,
                "درگاه پرداخت در حالت آزمایشی است و فقط برای آزمون مجاز است.",
            )
            return redirect("cart:detail")

        if not settings.DEBUG and get_default_gateway_name() == "mock_gateway":
            messages.error(
                request,
                "درگاه پرداخت واقعی هنوز برای محیط تولید پیکربندی نشده است.",
            )
            return redirect("cart:detail")

        try:
            payment, order, initiation = PaymentService.initiate(
                user=request.user,
                order_number=order_number,
            )
        except PaymentReconciliationRequired as exc:
            messages.error(request, str(exc))
            return redirect("accounts:dashboard_orders")
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect(
                "orders:payment_failed",
                order_number=order_number,
            )

        if payment is None:
            messages.info(request, "این سفارش قبلاً پرداخت شده است.")
            return redirect(
                "orders:payment_success",
                order_number=order.order_number,
            )

        return redirect(initiation.redirect_url)


class PaymentCallbackView(View):
    """HTTP-only callback endpoint; payment rules live in PaymentService."""

    def get(self, request, *args, **kwargs):
        return self.handle_callback(request, kwargs.get("gateway_name"))

    def post(self, request, *args, **kwargs):
        return self.handle_callback(request, kwargs.get("gateway_name"))

    def handle_callback(self, request, gateway_name=None):
        gateway_name = (
            gateway_name
            or request.POST.get("gateway")
            or request.GET.get("gateway")
            or get_default_gateway_name()
        )

        if gateway_name == "mock_gateway" and not settings.DEBUG:
            raise Http404

        data = {
            **request.GET.dict(),
            **request.POST.dict(),
        }

        try:
            outcome = PaymentService.handle_callback(
                gateway_name=gateway_name,
                data=data,
            )
        except (GatewayVerificationError, ValidationError) as exc:
            logger.warning(
                "Payment callback rejected: gateway=%s",
                gateway_name,
            )
            return HttpResponseBadRequest(str(exc))

        redirect_name = (
            "orders:payment_success"
            if outcome.success
            else "orders:payment_failed"
        )
        return redirect(
            redirect_name,
            order_number=outcome.order.order_number,
        )


class PaymentFailedView(LoginRequiredMixin, TemplateView):
    template_name = "orders/payment/failed.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order_number = self.kwargs.get("order_number")

        order = get_object_or_404(
            Order.objects.select_related("user"),
            order_number=order_number,
            user=self.request.user,
        )

        payment = Payment.objects.filter(order=order).order_by("-id").first()

        if payment is None:
            raise Http404("پرداختی برای این سفارش پیدا نشد.")

        context["order"] = order
        context["payment"] = payment
        context["can_retry_payment"] = order.status in {
            Order.Status.PENDING,
            Order.Status.PLACED,
        }
        return context


def mock_payment_gateway_view(request):
    if not settings.DEBUG or not request.user.is_authenticated:
        raise Http404

    trxid = request.GET.get("trxid")
    order_number = request.GET.get("order")

    if not trxid or not order_number:
        messages.error(request, "اطلاعات تراکنش نامعتبر است.")
        return redirect("cart:detail")

    payment = get_object_or_404(
        Payment.objects.select_related("order"),
        transaction_code=trxid,
        order__order_number=order_number,
        order__user=request.user,
        gateway_name="mock_gateway",
        status=Payment.Status.PENDING,
    )

    if payment.order.status not in {Order.Status.PENDING, Order.Status.PLACED}:
        raise Http404

    context = {
        "trxid": payment.transaction_code,
        "order_number": payment.order.order_number,
        "amount": payment.amount,
        "signature": build_mock_callback_signature(
            transaction_id=payment.transaction_code,
            amount=payment.amount,
        ),
        "callback_url": reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": "mock_gateway"},
        ),
    }
    return render(request, "orders/payment/mock_gateway.html", context)


class PaymentSuccessView(LoginRequiredMixin, TemplateView):
    template_name = "orders/payment/success.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order_number = self.kwargs.get("order_number")

        order = get_object_or_404(
            Order.objects.select_related("user"),
            order_number=order_number,
            user=self.request.user,
        )

        payment_record = (
            Payment.objects.filter(order=order, status=Payment.Status.SUCCESS)
            .order_by("-updated")
            .first()
        )

        if not payment_record:
            raise Http404("هیچ تراکنش موفقی برای این سفارش ثبت نشده است.")

        context["order"] = order
        context["payment"] = payment_record
        return context
