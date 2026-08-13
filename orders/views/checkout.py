# order/views/checkout.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from cart.models import Cart
from accounts.models.address import Address
from orders.utils import calculate_order_totals
from orders.services import OrderService
from orders.models.orders import Order
from orders.models.shipping import ShippingMethod
from decimal import Decimal


@login_required
def checkout_view(request):
    user = request.user
    cart = get_object_or_404(Cart, user=user, is_active=True)

    if not cart.items.exists():
        return redirect(reverse("cart_detail"))

    # --- دریافت آدرس پیش‌فرض کاربر ---
    shipping_address = Address.objects.filter(user=user, is_default=True).first()
    if not shipping_address:
        request.session["checkout_redirect_from"] = reverse("checkout")
        # فرض می‌کنیم آدرس address_list رو در accounts app داریم
        return redirect(reverse("address_list"))

    # --- دریافت روش‌های ارسال فعال ---
    active_shipping_methods = ShippingMethod.objects.filter(is_active=True)
    shipping_methods_data = []

    for method in active_shipping_methods:
        # محاسبه هزینه ارسال با استفاده از متد اصلاح شده مدل
        # cart.total_price رو به Decimal تبدیل می‌کنیم
        cost = method.calculate_shipping_cost(Decimal(str(cart.total_price)))
        # اگر هزینه ارسال صفر بود (یعنی رایگان شده)، نمایش بده
        formatted_cost = f"{cost:,.0f}".replace(",", ".")  # فرمت فارسی بدون اعشار
        shipping_methods_data.append(
            {
                "method": method,
                "cost": cost,  # هزینه خام برای جاوااسکریپت
                "formatted_cost": formatted_cost,  # هزینه فرمت شده برای نمایش
            }
        )

    if request.method == "POST":
        selected_shipping_method_id = request.POST.get("shipping_method_id")
        selected_shipping_method = None
        shipping_cost = Decimal(0)

        if not selected_shipping_method_id:
            # اگر کاربر هیچ روش ارسالی انتخاب نکرده
            request.session["checkout_error"] = "لطفاً یک روش ارسال انتخاب کنید."
            # باید صفحه رو دوباره با خطاها رندر کنیم
            return redirect(reverse("checkout"))

        try:
            selected_shipping_method = ShippingMethod.objects.get(
                id=selected_shipping_method_id, is_active=True
            )
            # دوباره محاسبه هزینه ارسال بر اساس مبلغ کل سبد خرید
            shipping_cost = selected_shipping_method.calculate_shipping_cost(
                Decimal(str(cart.total_price))
            )
        except (ShippingMethod.DoesNotExist, ValueError):
            request.session["checkout_error"] = "روش ارسال انتخاب شده معتبر نیست."
            return redirect(reverse("checkout"))

        # --- اگر همه چیز درست بود، سفارش رو ایجاد کن ---
        try:
            # فراخوانی متد اتمیک از سرویس
            order = OrderService.create_order(
                user=user,
                cart=cart,
                shipping_address=shipping_address,  # آدرس پیش‌فرض استفاده میشه
                customer_note=request.POST.get("customer_note", ""),
                shipping_method=selected_shipping_method,
                shipping_cost=shipping_cost,
            )

            # انتقال به صفحه تایید سفارش
            return redirect(
                reverse(
                    "order_confirmation", kwargs={"order_number": order.order_number}
                )
            )

        except Exception as e:
            # مدیریت خطا مطابق با پروتکل لاگ
            print(
                f"Checkout error: {e}"
            )  # در محیط واقعی از لاگینگ حرفه‌ای استفاده کنید
            request.session["checkout_error"] = (
                "خطایی در ثبت سفارش رخ داد. لطفاً مجدداً تلاش کنید."
            )
            return redirect(reverse("cart_detail"))

    else:
        # هندلینگ GET برای نمایش فرم
        # محاسبه نهایی برای نمایش در صفحه
        totals = calculate_order_totals(cart, shipping_address)
        context = {
            "cart": cart,
            "cart_items": cart.items.all(),
            "shipping_address": shipping_address,
            "customer_note": "",
            "totals": totals,
            "shipping_methods_data": shipping_methods_data,  # ارسال لیست روش‌های ارسال به قالب
            "selected_shipping_method_id": None,  # در حالت GET، هیچ روش ارسالی انتخاب نشده
            "shipping_cost": Decimal(0),  # هزینه ارسال اولیه صفر است
        }
        return render(request, "orders/checkout/checkout.html", context)
