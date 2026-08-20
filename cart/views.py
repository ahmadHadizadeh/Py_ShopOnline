# cart/view.py
import logging
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.http import HttpResponse
from catalog.models import Product
from .models import CartItem, Cart
from django.db import transaction
from django.urls import reverse
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from accounts.models.address import Address
from accounts.forms import AddressForm, PROVINCES_AND_CITIES
from orders.models.order_item import OrderItem
from orders.models.orders import Order
from orders.models.order_address_snapshot import OrderAddressSnapshot
from orders.models.payment import Payment
from orders.models.shipping import ShippingMethod
from decimal import Decimal
from cart.services import (
    add_product_to_cart,
    get_or_create_cart,
    remove_product_from_cart,
    toggle_cart_item_status,
    update_cart_item_quantity,
)
from django.core.exceptions import ValidationError
from orders.services import OrderService

logger = logging.getLogger(__name__)


def _parse_quantity(raw_value, default=1):
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def cart_detail(request):
    cart = get_or_create_cart(request)

    active_items = (
        cart.items.filter(status=CartItem.STATUS_ACTIVE).select_related("product").all()
    )
    saved_items = (
        cart.items.filter(status=CartItem.STATUS_SAVED).select_related("product").all()
    )

    context = {
        "cart": cart,
        "active_items": active_items,
        "saved_items": saved_items,
    }
    return render(request, "cart/detail.html", context)


@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)

    if not product.is_available:
        messages.error(request, "این محصول در حال حاضر موجود نیست.")
        return redirect(product.get_absolute_url())

    quantity = max(1, _parse_quantity(request.POST.get("quantity", 1), default=1))
    cart = get_or_create_cart(request)

    try:
        add_product_to_cart(cart=cart, product=product, quantity=quantity)
    except ValidationError as exc:
        messages.error(request, exc.message)
        return redirect(product.get_absolute_url())

    messages.success(request, "محصول به سبد خرید اضافه شد.")
    return redirect("cart:detail")


@require_POST
def update_cart_item(request, item_id):
    cart = get_or_create_cart(request)

    item = get_object_or_404(
        CartItem.objects.select_related("product"),
        pk=item_id,
        cart=cart,
    )

    quantity = _parse_quantity(request.POST.get("quantity", 1), default=1)

    if quantity <= 0:
        remove_product_from_cart(cart=cart, product_id=item.product_id)
        messages.info(request, "محصول از سبد خرید حذف شد.")
        return redirect("cart:detail")

    try:
        update_cart_item_quantity(
            cart=cart,
            product_id=item.product_id,
            quantity=quantity,
        )
    except ValidationError as exc:
        messages.error(request, exc.message)
        return redirect("cart:detail")

    messages.success(request, "تعداد محصول به‌روزرسانی شد.")
    return redirect("cart:detail")


@require_POST
def remove_cart_item(request, item_id):
    cart = get_or_create_cart(request)

    item = get_object_or_404(CartItem, pk=item_id, cart=cart)
    remove_product_from_cart(cart=cart, product_id=item.product_id)

    messages.info(request, "محصول از سبد خرید حذف شد.")
    return redirect("cart:detail")


@require_POST
def save_for_later(request, item_id):
    cart = get_or_create_cart(request)

    item = get_object_or_404(CartItem, pk=item_id, cart=cart)

    try:
        toggle_cart_item_status(
            cart=cart,
            product_id=item.product_id,
            to_status=CartItem.STATUS_SAVED,
        )
    except ValidationError as exc:
        messages.error(request, exc.message)
        return redirect("cart:detail")

    messages.success(request, "محصول به لیست ذخیره‌شده‌ها منتقل شد.")
    return redirect("cart:detail")


@require_POST
def move_to_cart(request, item_id):
    cart = get_or_create_cart(request)

    item = get_object_or_404(
        CartItem.objects.select_related("product"),
        pk=item_id,
        cart=cart,
    )

    try:
        toggle_cart_item_status(
            cart=cart,
            product_id=item.product_id,
            to_status=CartItem.STATUS_ACTIVE,
        )
    except ValidationError as exc:
        messages.error(request, exc.message)
        return redirect("cart:detail")

    messages.success(request, "محصول دوباره به سبد خرید منتقل شد.")
    return redirect("cart:detail")


# cart/views.py/class CheckoutView(View)

from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect


class CartDetailView(View):
    template_name = "cart/detail.html"

    def get(self, request):
        cart = None
        if request.user.is_authenticated:
            cart = Cart.objects.filter(
                user=request.user, status=Cart.STATUS_ACTIVE
            ).first()
        else:
            cart_id = request.session.get("cart_id")
            if cart_id:
                cart = Cart.objects.filter(id=cart_id).first()

        return render(request, self.template_name, {"cart": cart})


class CheckoutView(View):
    template_name = "cart/checkout.html"

    def get_address_form(self, user):
        default_address = Address.objects.filter(user=user, is_default=True).first()
        if default_address:
            return AddressForm(instance=default_address)
        return AddressForm()

    def get_cart_queryset(self):
        return Cart.objects.prefetch_related(
            "items__product",
        )

    def get_cart(self, request, for_update=False):
        queryset = self.get_cart_queryset()

        if for_update:
            queryset = queryset.select_for_update()

        if request.user.is_authenticated:
            return queryset.filter(
                user=request.user,
                status=Cart.STATUS_ACTIVE,
            ).first()

        cart_id = request.session.get("cart_id")

        if not cart_id:
            return None

        return queryset.filter(
            id=cart_id,
            status=Cart.STATUS_ACTIVE,
            user__isnull=True,
        ).first()

    def get_shipping_methods_data(self, request, cart):
        shipping_methods_with_costs = []
        if not cart or cart.total_price is None:
            return shipping_methods_with_costs

        try:
            active_shipping_methods = ShippingMethod.objects.filter(is_active=True)
            for method in active_shipping_methods:
                try:
                    cost = method.calculate_shipping_cost(cart.total_price)
                    formatted_cost_display = f"{cost:,}".replace(",", ".")
                    shipping_methods_with_costs.append(
                        {
                            "method": method,
                            "cost": cost,
                            "formatted_cost": formatted_cost_display,
                        }
                    )
                except Exception as e:
                    logger.error(f"Error calculating shipping: {e}")
                    shipping_methods_with_costs.append(
                        {"method": method, "cost": 0, "formatted_cost": "0"}
                    )
        except Exception as e:
            logger.error(f"Error fetching shipping methods: {e}")
            messages.error(request, "امکان محاسبه هزینه‌های ارسال وجود ندارد.")

        return shipping_methods_with_costs

    def get_context_data(self, request, cart=None, address_form=None):
        user = request.user
        if user.is_authenticated:
            if address_form is None:
                address_form = self.get_address_form(user)
            user_addresses = Address.objects.filter(user=user).order_by(
                "-is_default", "-id"
            )
        else:
            if address_form is None:
                address_form = AddressForm()
            user_addresses = Address.objects.none()

        return {
            "cart": cart,
            "form": address_form,
            "user_addresses": user_addresses,
            "shipping_methods_data": self.get_shipping_methods_data(request, cart),
            "province_cities": PROVINCES_AND_CITIES,
        }

    def get(self, request):
        cart = self.get_cart(request)
        if not cart or not cart.items.exists():
            messages.warning(request, "سبد خرید شما خالی است.")
            return redirect("cart:detail")

        return render(
            request, self.template_name, self.get_context_data(request, cart=cart)
        )

    @transaction.atomic
    def post(self, request):
        cart = self.get_cart(request, for_update=True)
        if not cart or not cart.items.exists():
            messages.error(request, "سبد خرید معتبر نیست.")
            return redirect("cart:detail")

        shipping_method_id = request.POST.get("shipping_method_id")
        shipping_method = ShippingMethod.objects.filter(
            id=shipping_method_id, is_active=True
        ).first()

        if not shipping_method:
            messages.error(request, "لطفاً روش ارسال معتبری انتخاب کنید.")
            return render(
                request, self.template_name, self.get_context_data(request, cart=cart)
            )

        address_id = request.POST.get("address_id")
        address_instance = (
            Address.objects.filter(id=address_id, user=request.user).first()
            if (address_id and request.user.is_authenticated)
            else None
        )

        address_form = AddressForm(request.POST, instance=address_instance)
        if not address_form.is_valid():
            return render(
                request,
                self.template_name,
                self.get_context_data(request, cart=cart, address_form=address_form),
            )

        address = address_form.save(commit=False)
        if request.user.is_authenticated:
            address.user = request.user
            address.save()
            if address.is_default:
                Address.objects.filter(user=request.user).exclude(id=address.id).update(
                    is_default=False
                )
        else:
            address.user = None
            address.save()

        try:
            order = OrderService.create_order(
                user=request.user if request.user.is_authenticated else None,
                cart=cart,
                shipping_method=shipping_method,
                shipping_address=address,
                customer_note=request.POST.get("customer_note", "").strip(),
            )
        except ValidationError as exc:
            messages.error(
                request,
                (
                    exc.messages[0]
                    if hasattr(exc, "messages") and exc.messages
                    else str(exc)
                ),
            )
            return render(
                request,
                self.template_name,
                self.get_context_data(request, cart=cart, address_form=address_form),
            )

        if not request.user.is_authenticated and "cart_id" in request.session:
            del request.session["cart_id"]

        return redirect(
            reverse(
                "orders:order_confirmation", kwargs={"order_number": order.order_number}
            )
        )

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and not request.user.is_authenticated:
            messages.warning(
                request,
                "لطفاً برای تکمیل خرید ابتدا وارد حساب کاربری خود شوید.",
            )

            login_url = getattr(settings, "LOGIN_URL", "/accounts/login/")
            query_string = urlencode({"next": request.get_full_path()})

            return redirect(f"{login_url}?{query_string}")

        return super().dispatch(request, *args, **kwargs)
