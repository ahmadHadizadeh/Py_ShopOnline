# orders/views/checkout.py
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models.address import Address
from cart.models import Cart
from orders.models import ShippingMethod
from orders.services import OrderService


@login_required
def checkout_view(request):
    user = request.user
    # پیدا کردن سبد خرید فعال کاربر
    cart = get_object_or_404(Cart, user=user, status=Cart.STATUS_ACTIVE)

    if not cart.items.exists():
        messages.warning(request, "سبد خرید شما خالی است.")
        return redirect("cart:detail")

    # بررسی وجود آدرس
    addresses = Address.objects.filter(user=user).order_by("-is_default", "-id")
    if not addresses.exists():
        messages.info(request, "لطفاً ابتدا آدرس خود را ثبت کنید.")
        return redirect("accounts:address_list")

    # دریافت روش‌های ارسال فعال
    shipping_methods = ShippingMethod.objects.filter(is_active=True).order_by("cost")
    if not shipping_methods.exists():
        messages.error(request, "در حال حاضر هیچ روش ارسالی فعال نیست.")
        return redirect("cart:detail")

    # پیش‌فرض: اولین روش ارسال یا آدرس پیش‌فرض
    selected_address = addresses.filter(is_default=True).first() or addresses.first()
    selected_shipping_method = shipping_methods.first()

    if request.method == "POST":
        address_id = request.POST.get("address_id")
        shipping_method_id = request.POST.get("shipping_method_id")
        customer_note = request.POST.get("customer_note", "").strip()

        # اعتبارسنجی ورودی‌ها
        address = addresses.filter(id=address_id).first()
        method = shipping_methods.filter(id=shipping_method_id).first()

        if not address or not method:
            messages.error(request, "لطفاً آدرس و روش ارسال معتبری انتخاب کنید.")
        else:
            try:
                order = OrderService.create_order(
                    user=user,
                    cart=cart,
                    shipping_address=address,
                    shipping_method=method,
                    customer_note=customer_note,
                )
                return redirect(
                    reverse(
                        "orders:order_confirmation",
                        kwargs={"order_number": order.order_number},
                    )
                )
            except Exception as e:
                # لاگ خطا برای دیباگ (در محیط عملیاتی از logger استفاده شود)
                print(f"Checkout Error: {str(e)}")
                messages.error(request, "متأسفانه خطایی در ثبت سفارش رخ داد.")

    # محاسبات مبالغ برای نمایش در صفحه
    totals = OrderService.calculate_order_totals(cart, selected_shipping_method)

    context = {
        "cart": cart,
        "addresses": addresses,
        "shipping_methods": shipping_methods,
        "selected_address": selected_address,
        "selected_shipping_method": selected_shipping_method,
        "totals": totals,
    }
    return render(request, "cart/checkout.html", context)
