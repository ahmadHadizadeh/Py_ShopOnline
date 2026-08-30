import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from cart.models import Cart
from orders.models.orders import Order
from orders.models.payment import Payment
from decimal import Decimal, InvalidOperation

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
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update(),
                order_number=order_number,
                user=request.user,
            )

            # ۱. جلوگیری از پرداخت مجدد سفارش‌هایی که پرداخت شده یا پردازش شده‌اند
            non_payable_statuses = {
                getattr(Order.Status, "PAID", "PAID"),
                getattr(Order.Status, "PROCESSING", "PROCESSING"),
                getattr(Order.Status, "COMPLETED", "COMPLETED"),
            }
            if hasattr(Order, "Status") and order.status in non_payable_statuses:
                messages.info(request, "این سفارش قبلاً پرداخت شده است.")
                return redirect(
                    "orders:payment_success", order_number=order.order_number
                )

            # ۲. در تلاش مجدد، وضعیت سفارش به PENDING بازمی‌گردد
            if hasattr(Order, "Status") and order.status != getattr(
                Order.Status, "PENDING", "PENDING"
            ):
                order.status = getattr(Order.Status, "PENDING", "PENDING")
                order.save(update_fields=["status"])

            # ۳. رعایت قید OneToOne: بازیابی همان رکورد پرداخت موجود یا ایجاد فقط در صورت عدم وجود
            payment = Payment.objects.select_for_update().filter(order=order).first()

            gateway_transaction_id = f"TRX-{order.id}-{int(timezone.now().timestamp())}"

            if payment is None:
                payment = Payment.objects.create(
                    order=order,
                    user=request.user,
                    amount=order.final_amount,
                    status=Payment.Status.PENDING,
                    gateway_name="mock_gateway",
                    transaction_code=gateway_transaction_id,
                )
            else:
                # به‌روزرسانی همان رکورد موجود به جای INSERT مجدد
                payment.status = Payment.Status.PENDING
                payment.transaction_code = gateway_transaction_id
                payment.amount = order.final_amount
                payment.gateway_name = "mock_gateway"
                payment.save(
                    update_fields=[
                        "status",
                        "transaction_code",
                        "amount",
                        "gateway_name",
                    ]
                )

        # هدایت ایمن به درگاه با تراکنش به‌روزشده
        payment_gateway_url = reverse("orders:mock_payment_gateway")
        redirect_url = (
            f"{payment_gateway_url}"
            f"?trxid={payment.transaction_code}"
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
                    Payment.objects.select_related("order", "user")
                    .select_for_update()
                    .get(transaction_code=trxid)
                )

                if payment.user_id != request.user.id:
                    return HttpResponseBadRequest("دسترسی به این تراکنش مجاز نیست.")

                order = Order.objects.select_for_update().get(pk=payment.order_id)

                if payment.status == Payment.Status.SUCCESS:
                    return redirect(
                        reverse(
                            "orders:payment_success",
                            kwargs={"order_number": order.order_number},
                        )
                    )

                gateway_amount = request.POST.get("amount") or request.GET.get("amount")
                if gateway_amount not in (None, ""):
                    try:
                        if Decimal(str(gateway_amount)) != payment.amount:
                            logger.warning(
                                "Payment callback amount mismatch: payment_id=%s trxid=%s expected=%s got=%s",
                                payment.id,
                                trxid,
                                payment.amount,
                                gateway_amount,
                            )
                            return HttpResponseBadRequest(
                                "مبلغ تراکنش با سفارش مطابقت ندارد."
                            )
                    except (InvalidOperation, TypeError, ValueError):
                        return HttpResponseBadRequest("مبلغ تراکنش نامعتبر است.")

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
