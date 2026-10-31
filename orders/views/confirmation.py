# orders/views/confirmation.py
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from orders.models.orders import Order
from orders.models.payment import Payment

logger = logging.getLogger(__name__)


@login_required
def order_confirmation_view(request, order_number):
    # دریافت سفارش به همراه بهینه‌سازی کامل کوئری‌ها (شامل آدرس و آیتم‌ها)
    order = get_object_or_404(
        Order.objects.select_related("payment", "address_snapshot").prefetch_related(
            "items"
        ),
        order_number=order_number,
        user=request.user,
    )

    payment = getattr(order, "payment", None)
    if payment is not None and payment.status == Payment.Status.SUCCESS:
        # The receipt explicitly flags any order/payment/inventory mismatch.
        return redirect("orders:payment_success", order_number=order.order_number)

    if order.status == Order.Status.CANCELLED:
        messages.error(request, "این سفارش لغو شده و امکان پرداخت آن وجود ندارد.")
        return redirect("accounts:dashboard_orders")

    if order.status in {
        Order.Status.PAID,
        Order.Status.PROCESSING,
        Order.Status.COMPLETED,
    }:
        messages.error(
            request,
            "وضعیت سفارش با سابقه پرداخت سازگار نیست؛ برای بررسی به پشتیبانی مراجعه کنید.",
        )
        return redirect("accounts:dashboard_orders")

    if request.method == "POST":
        # هدایت به شروع فرآیند پرداخت و اتصال به درگاه
        return redirect("orders:initiate_payment", order_number=order.order_number)

    context = {
        "order": order,
        "payment": getattr(order, "payment", None),
        "payment_amount": order.final_amount,
        "order_address": order.address_snapshot,
    }

    return render(request, "orders/payment/checkout_confirmation.html", context)
