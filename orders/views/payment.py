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
from cart.models import Cart
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
        order = get_object_or_404(
            Order,
            order_number=order_number,
            user=request.user,
            status=Order.Status.PENDING,
        )

        payment = (
            Payment.objects.filter(
                order=order,
                status=Payment.Status.PENDING,
            )
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

        gateway_transaction_id = f"TRX-{payment.id}-{int(timezone.now().timestamp())}"
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


class PaymentCallbackView(LoginRequiredMixin, TemplateView):
    login_url = "/accounts/login/"

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.handle_callback(request)

    def post(self, request, *args, **kwargs):
        return self.handle_callback(request)

    def handle_callback(self, request):
        trxid = request.POST.get("trxid") or request.GET.get("trxid")
        status = request.POST.get("status") or request.GET.get("status")

        if not trxid:
            return HttpResponseBadRequest("شناسه تراکنش نامعتبر است.")

        try:
            with transaction.atomic():
                payment = (
                    Payment.objects.select_related("order")
                    .select_for_update()
                    .get(transaction_code=trxid)
                )
                order = Order.objects.select_for_update().get(pk=payment.order_id)

                if payment.status == Payment.Status.SUCCESS:
                    return redirect(
                        reverse(
                            "orders:payment_success",
                            kwargs={"order_number": order.order_number},
                        )
                    )

                if payment.status == Payment.Status.FAILED:
                    return redirect(
                        reverse(
                            "orders:payment_failed",
                            kwargs={"order_number": order.order_number},
                        )
                    )

                is_success = status == "success"
                gateway_response = {
                    "trxid": trxid,
                    "status": status,
                    **request.POST.dict(),
                    **request.GET.dict(),
                }

                if is_success:
                    ref_id = (
                        f"REF-{payment.id}-{timezone.now().strftime('%Y%m%d%H%M%S')}"
                    )
                    payment.update_status_and_order(
                        new_status=Payment.Status.SUCCESS,
                        transaction_id=trxid,
                        reference_id=ref_id,
                        gateway_response=str(gateway_response),
                    )

                    if order.cart_id:
                        try:
                            cart = Cart.objects.select_for_update().get(
                                pk=order.cart_id
                            )
                            cart.items.all().delete()
                            cart.delete()
                        except Cart.DoesNotExist:
                            pass
                else:
                    payment.update_status_and_order(
                        new_status=Payment.Status.FAILED,
                        transaction_id=trxid,
                        reference_id=None,
                        gateway_response=str(gateway_response),
                    )

            return redirect(
                reverse(
                    "orders:payment_success" if is_success else "orders:payment_failed",
                    kwargs={"order_number": order.order_number},
                )
            )

        except Payment.DoesNotExist:
            return HttpResponseBadRequest("تراکنش پیدا نشد.")


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
