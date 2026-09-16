import logging

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
from orders.payment.gateways import GatewayVerificationError
from orders.payment.services import (
    DEFAULT_GATEWAY_NAME,
    PaymentService,
)

logger = logging.getLogger(__name__)


class ProcessPaymentView(LoginRequiredMixin, View):
    login_url = "/accounts/login/"

    def get(self, request, order_number):
        return self._start_payment(request, order_number)

    def post(self, request, order_number):
        return self._start_payment(request, order_number)

    def _start_payment(self, request, order_number):
        try:
            payment, order, initiation = PaymentService.initiate(
                user=request.user,
                order_number=order_number,
            )
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
            or DEFAULT_GATEWAY_NAME
        )

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
        return context


def mock_payment_gateway_view(request):
    trxid = request.GET.get("trxid")
    order_number = request.GET.get("order")
    amount = request.GET.get("amount")

    if not trxid or not order_number or not amount:
        messages.error(request, "اطلاعات تراکنش نامعتبر است.")
        return redirect("cart:detail")

    context = {
        "trxid": trxid,
        "order_number": order_number,
        "amount": amount,
        "callback_url": reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": DEFAULT_GATEWAY_NAME},
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
