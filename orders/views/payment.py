import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.utils import timezone
from django.views.generic import TemplateView, ListView, DetailView
from orders.models.payment import Payment
from orders.models.orders import Order
import jdatetime

logger = logging.getLogger(__name__)


class ProcessPaymentView(LoginRequiredMixin, View):
    login_url = "/accounts/login/"

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, order_number):
        return self._start_payment(request, order_number)

    def post(self, request, order_number):
        return self._start_payment(request, order_number)

    def _start_payment(self, request, order_number):
        try:
            order = get_object_or_404(
                Order,
                order_number=order_number,
                user=request.user,
                status=Order.Status.PENDING,
            )

            payment = (
                Payment.objects.filter(order=order, status=Payment.Status.PENDING)
                .order_by("-created")
                .first()
            )

            if payment is None:
                payment = Payment.objects.create(
                    order=order,
                    user=request.user,
                    amount=order.final_amount,
                    status=Payment.Status.PENDING,
                    gateway_name="mock_gateway",
                )

            gateway_transaction_id = (
                f"TRX-{payment.id}-{int(timezone.now().timestamp())}"
            )
            payment.transaction_code = gateway_transaction_id
            payment.save(update_fields=["transaction_code"])

            payment_gateway_url = reverse("orders:mock_payment_gateway")
            redirect_url = (
                f"{payment_gateway_url}"
                f"?trxid={gateway_transaction_id}"
                f"&order={order.order_number}"
                f"&amount={payment.amount}"
            )
            return redirect(redirect_url)

        except Exception:
            logger.exception(
                "Unexpected error in ProcessPaymentView for order %s", order_number
            )
            request.session["checkout_error"] = (
                "خطای سیستمی در شروع فرآیند پرداخت رخ داد."
            )
            return redirect("cart:detail")


class PaymentCallbackView(View):
    def get(self, request):
        trxid = request.GET.get("trxid")
        status = request.GET.get("status")

        if not trxid:
            return HttpResponseBadRequest("خطا: شناسه تراکنش نامعتبر است.")

        try:
            payment = Payment.objects.select_related("order").get(
                transaction_code=trxid
            )

            # Idempotency check: if status is already FINAL (Success/Failed), just redirect
            if payment.status in [Payment.Status.SUCCESS, Payment.Status.FAILED]:
                url_name = (
                    "orders:payment_success"
                    if payment.status == Payment.Status.SUCCESS
                    else "orders:payment_failed"
                )
                return redirect(
                    reverse(
                        url_name, kwargs={"order_number": payment.order.order_number}
                    )
                )

            is_verified = status == "success"
            new_status = (
                Payment.Status.SUCCESS if is_verified else Payment.Status.FAILED
            )
            ref_id = (
                f"REF-{payment.id}-{int(timezone.now().timestamp())}"
                if is_verified
                else None
            )

            payment.update_status_and_order(
                new_status=new_status,
                reference_id=ref_id,
                gateway_response=str(request.GET.dict()),
            )

            url_name = (
                "orders:payment_success" if is_verified else "orders:payment_failed"
            )
            return redirect(
                reverse(url_name, kwargs={"order_number": payment.order.order_number})
            )

        except Payment.DoesNotExist:
            return HttpResponseBadRequest("خطا: تراکنش یافت نشد.")
        except Exception:
            return HttpResponseBadRequest("خطای سیستمی در پردازش.")


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
        "callback_url": reverse("orders:payment_callback"),
    }
    return render(request, "orders/payment/mock_gateway.html", context)


class PaymentSuccessView(LoginRequiredMixin, TemplateView):
    template_name = "orders/payment/success.html"

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



class PaymentFailedView(LoginRequiredMixin, TemplateView):
    template_name = "orders/payment/checkout_confirmation.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order_number = self.kwargs.get("order_number")

        order = get_object_or_404(
            Order.objects.select_related("user"),
            order_number=order_number,
            user=self.request.user,
        )

        payment_record = (
            Payment.objects.filter(order=order, status=Payment.Status.FAILED)
            .order_by("-updated")
            .first()
        )

        if not payment_record:
            raise Http404("هیچ تراکنش ناموفقی برای این سفارش ثبت نشده است.")

        context["order"] = order
        context["payment"] = payment_record
        context["message"] = "پرداخت با خطا مواجه شد."
        return context


class OrderListView(LoginRequiredMixin, ListView):
    model = Order
    template_name = "orders/dashboard/order_list.html"
    context_object_name = "orders"
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for order in context["orders"]:
            order.jalali_created = jdatetime.date.fromgregorian(
                date=order.created.date()
            ).strftime("%Y/%m/%d")
        return context

    def get_queryset(self):
        return self.request.user.orders.all().order_by("-created")


class OrderDetailView(LoginRequiredMixin, DetailView):
    model = Order
    template_name = "orders/dashboard/order_detail.html"
    context_object_name = "order"
    slug_field = "order_number"
    slug_url_kwarg = "order_number"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = context["order"]
        context["jalali_created"] = jdatetime.datetime.fromgregorian(
            datetime=order.created
        ).strftime("%Y/%m/%d - %H:%M")
        return context

    def get_queryset(self):
        return self.request.user.orders.all().prefetch_related("items")
