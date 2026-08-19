# order/views/checkout.py


from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models.address import Address
from cart.models import Cart
from orders.models.shipping import ShippingMethod
from orders.services import OrderService
from orders.utils import calculate_order_totals
from orders.models.orders import Order


# orders/views/checkout.py/def checkout_view(request):19-118
@login_required
def checkout_view(request):
    user = request.user
    cart = get_object_or_404(Cart, user=user, is_active=True)

    if not cart.items.exists():
        return redirect(reverse("cart:detail"))

    shipping_address = Address.objects.filter(user=user, is_default=True).first()
    if not shipping_address:
        request.session["checkout_redirect_from"] = reverse("cart:checkout")
        return redirect(reverse("accounts:address_list"))

    active_shipping_methods = ShippingMethod.objects.filter(is_active=True)
    shipping_methods_data = []

    for method in active_shipping_methods:
        cost = method.calculate_shipping_cost(Decimal(str(cart.total_price)))
        formatted_cost = f"{cost:,.0f}".replace(",", ".")
        shipping_methods_data.append(
            {
                "method": method,
                "cost": cost,
                "formatted_cost": formatted_cost,
            }
        )

    if request.method == "POST":
        selected_shipping_method_id = request.POST.get("shipping_method_id")
        selected_address_id = request.POST.get("address_id")
        customer_note = request.POST.get("customer_note", "").strip()

        selected_shipping_method = None
        shipping_cost = Decimal(0)

        if selected_address_id:
            shipping_address = Address.objects.filter(
                id=selected_address_id,
                user=user,
            ).first()

        if not shipping_address:
            request.session["checkout_error"] = "لطفاً یک آدرس معتبر انتخاب کنید."
            return redirect(reverse("accounts:address_list"))

        if not selected_shipping_method_id:
            request.session["checkout_error"] = "لطفاً یک روش ارسال انتخاب کنید."
            return redirect(reverse("cart:checkout"))

        try:
            selected_shipping_method = ShippingMethod.objects.get(
                id=selected_shipping_method_id,
                is_active=True,
            )
            shipping_cost = selected_shipping_method.calculate_shipping_cost(
                Decimal(str(cart.total_price))
            )
        except (ShippingMethod.DoesNotExist, ValueError):
            request.session["checkout_error"] = "روش ارسال انتخاب شده معتبر نیست."
            return redirect(reverse("cart:checkout"))

        try:
            order = OrderService.create_order(
                user=user,
                cart=cart,
                shipping_address=shipping_address,
                customer_note=customer_note,
                shipping_method=selected_shipping_method,
                shipping_cost=shipping_cost,
            )

            return redirect(
                reverse(
                    "orders:order_confirmation",
                    kwargs={"order_number": order.order_number},
                )
            )

        except Exception as e:
            print(f"Checkout error: {e}")
            request.session["checkout_error"] = (
                "خطایی در ثبت سفارش رخ داد. لطفاً مجدداً تلاش کنید."
            )
            return redirect(reverse("cart:detail"))

    totals = calculate_order_totals(cart, shipping_address)
    user_addresses = Address.objects.filter(user=user).order_by("-is_default", "-id")

    context = {
        "cart": cart,
        "cart_items": cart.items.all(),
        "shipping_address": shipping_address,
        "user_addresses": user_addresses,
        "customer_note": "",
        "totals": totals,
        "shipping_methods_data": shipping_methods_data,
        "selected_shipping_method_id": None,
        "shipping_cost": Decimal(0),
    }
    return render(request, "cart/checkout.html", context)
