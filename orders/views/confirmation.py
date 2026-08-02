# orders/views/confirmation.py
import logging
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

    # بررسی وضعیت پرداخت (استفاده ایمن از فیلد status و ارتباط با مدل Payment)
    is_paid = (order.status == Order.Status.PAID) or (
        hasattr(order, "payment")
        and order.payment
        and order.payment.status == Payment.Status.SUCCESS
    )

    if is_paid:
        # هدایت به صفحه تراکنش موفق
        return redirect("orders:payment_success", order_number=order.order_number)

    if request.method == "POST":
        # هدایت به شروع فرآیند پرداخت و اتصال به درگاه
        return redirect("orders:initiate_payment", order_number=order.order_number)

    context = {
        "order": order,
        "payment_amount": order.final_amount,
        "order_address": order.address_snapshot,
    }

    return render(request, "orders/payment/checkout_confirmation.html", context)
