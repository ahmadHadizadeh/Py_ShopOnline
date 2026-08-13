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


# def checkout_view(request):
#     return HttpResponse(request, "⭐ Add To Card Success | Ahmad ⭐")


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

class CheckoutView(View):
    template_name = "cart/checkout.html"

    def get_address_form(self, user):
        # تلاش برای یافتن آدرس پیش‌فرض کاربر
        default_address = Address.objects.filter(user=user, is_default=True).first()
        if default_address:
            # اگر آدرس پیش‌فرض وجود دارد، فرم را با آن مقداردهی اولیه کن
            return AddressForm(instance=default_address)
        # در غیر این صورت، فرم خالی برگردان
        return AddressForm()

    def get_context_data(self, request, cart=None, address_form=None):
        shipping_methods_with_costs = []

        if cart and cart.total_price is not None:
            try:
                # فیلتر کردن روش‌های ارسال فعال
                active_shipping_methods = ShippingMethod.objects.filter(is_active=True)
                for method in active_shipping_methods:
                    try:
                        # محاسبه هزینه ارسال برای هر روش بر اساس کل سبد خرید
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
                        logger.error(
                            f"Error calculating shipping cost for method {method.id} with cart total {cart.total_price}: {e}"
                        )
                        shipping_methods_with_costs.append(
                            {
                                "method": method,
                                "cost": 0,
                                "formatted_cost": "0",
                            }
                        )
            except Exception as e:
                logger.error(f"Error fetching or processing shipping methods: {e}")
                messages.error(
                    request,
                    "امکان محاسبه هزینه‌های ارسال وجود ندارد. لطفاً بعداً تلاش کنید.",
                )

        # بررسی احراز هویت کاربر برای فرم آدرس و لیست آدرس‌ها
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

        # ساخت دیکشنری context برای ارسال به قالب
        return {
            "cart": cart,
            "form": address_form,
            "user_addresses": user_addresses,
            "shipping_methods_data": shipping_methods_with_costs,
            "province_cities": PROVINCES_AND_CITIES,
        }

    def get(self, request):
        # استفاده از منطق احراز هویت برای استخراج سبد خرید (سازگار با کاربران مهمان و عضو)
        if request.user.is_authenticated:
            cart = (
                Cart.objects.filter(user=request.user)
                .prefetch_related("items__product")
                .first()
            )
        else:
            # در صورت پیاده‌سازی سبد با Session برای مهمان، از کلید سشن استفاده شود
            # فعلاً طبق ساختار فعلی پروژه برای مهمان:
            cart_id = request.session.get("cart_id")
            cart = (
                Cart.objects.filter(id=cart_id)
                .prefetch_related("items__product")
                .first()
            )

        # بررسی وجود و محتوای سبد خرید برای جلوگیری از ورود به صفحه پرداخت خالی
        if not cart or not cart.items.exists():
            messages.warning(request, "سبد خرید شما خالی است.")
            return redirect("cart:detail")

        # رندر کردن قالب با استفاده از متد context_data بهینه‌شده در مرحله قبل
        return render(
            request, self.template_name, self.get_context_data(request, cart=cart)
        )


    @transaction.atomic  # اجرای کل متد POST در یک تراکنش اتمیک
    def post(self, request):
        # دریافت سبد خرید کاربر (عضو یا مهمان) به صورت ایمن و قفل‌گذاری رکورد
        if request.user.is_authenticated:
            cart = (
                Cart.objects.select_for_update()
                .filter(user=request.user)
                .prefetch_related("items__product")
                .first()
            )
        else:
            cart_id = request.session.get("cart_id")
            cart = (
                Cart.objects.select_for_update()
                .filter(id=cart_id)
                .prefetch_related("items__product")
                .first()
            )

        if not cart:
            messages.error(request, "سبد خرید شما یافت نشد.")
            return redirect("cart:detail")

        cart_items = list(cart.items.all())
        if not cart_items:
            messages.error(request, "سبد خرید شما خالی است.")
            return redirect("cart:detail")

        # دریافت و بررسی شناسه روش ارسال
        shipping_method_id = request.POST.get("shipping_method_id")
        if not shipping_method_id:
            messages.error(request, "لطفاً روش ارسال را انتخاب کنید.")
            return render(
                request, self.template_name, self.get_context_data(request, cart)
            )

        shipping_method = ShippingMethod.objects.filter(
            id=shipping_method_id, is_active=True
        ).first()

        if not shipping_method:
            messages.error(request, "روش ارسال انتخاب شده معتبر نیست.")
            return render(
                request, self.template_name, self.get_context_data(request, cart)
            )

        # پردازش آدرس
        address_id = request.POST.get("address_id")
        address_instance = None

        # تنها در صورتی که کاربر لاگین است و شناسه آدرس ارسال شده، آدرس را واکشی می‌کنیم
        if address_id and request.user.is_authenticated:
            address_instance = Address.objects.filter(
                id=address_id,
                user=request.user,
            ).first()

        # ساخت فرم آدرس
        address_form = AddressForm(request.POST, instance=address_instance)

        # اعتبارسنجی فرم آدرس
        if not address_form.is_valid():
            return render(
                request,
                self.template_name,
                self.get_context_data(request, cart, address_form),
            )

        # ذخیره آدرس با مدیریت احراز هویت
        address = address_form.save(commit=False)
        if request.user.is_authenticated:
            address.user = request.user
            address.save()

            # مدیریت آدرس پیش‌فرض فقط برای کاربران لاگین شده
            if address.is_default:
                Address.objects.filter(user=request.user).exclude(id=address.id).update(
                    is_default=False
                )
        else:
            # برای مهمان آدرس بدون تعلق به کاربر خاص ذخیره می‌شود
            address.user = None
            address.save()

        # بررسی موجودی و فعال بودن محصولات در سبد خرید
        for item in cart_items:
            product = item.product
            product_title = getattr(product, "name", None) or getattr(
                product, "title", "محصول"
            )

            if not product.is_active:
                messages.error(request, f"محصول '{product_title}' دیگر فعال نیست.")
                return render(
                    request,
                    self.template_name,
                    self.get_context_data(request, cart, address_form),
                )

            if hasattr(product, "stock") and product.stock is not None:
                if product.stock < item.quantity:
                    messages.error(
                        request,
                        f"موجودی محصول '{product_title}' کافی نیست. (موجودی: {product.stock})",
                    )
                    return render(
                        request,
                        self.template_name,
                        self.get_context_data(request, cart, address_form),
                    )

        # محاسبه مبالغ نهایی
        subtotal_amount = Decimal("0.00")
        for item in cart_items:
            subtotal_amount += item.product.price * item.quantity

        try:
            shipping_amount = shipping_method.calculate_shipping_cost(subtotal_amount)
            if not isinstance(shipping_amount, (int, float, Decimal)):
                shipping_amount = Decimal("0.00")
        except Exception as e:
            logger.error(
                f"Error in calculate_shipping_cost for {shipping_method.id}: {e}"
            )
            shipping_amount = Decimal("0.00")

        final_amount = subtotal_amount + shipping_amount

        # ایجاد سفارش (اختصاص کاربر لاگین شده یا None برای مهمان)
        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            cart=cart,
            shipping_method=shipping_method,
            status=Order.Status.PENDING,
            subtotal_amount=subtotal_amount,
            shipping_amount=shipping_amount,
            final_amount=final_amount,
        )

        # ایجاد اسنپ‌شات از آدرس در زمان ثبت سفارش
        OrderAddressSnapshot.objects.create(
            order=order,
            recipient_name=address.recipient_name,
            recipient_mobile=address.phone_number,
            postal_code=address.postal_code,
            province=address.province,
            city=address.city,
            address_line=address.address_line,
        )

        # ایجاد آیتم‌های سفارش (OrderItems) به صورت بهینه
        order_items_to_create = []
        for item in cart_items:
            product = item.product
            variant = getattr(item, "variant", None)
            unit_price = product.price
            product_title = getattr(product, "name", None) or getattr(
                product, "title", ""
            )
            variant_name = getattr(variant, "name", "") if variant else ""
            sku = getattr(variant, "sku", None) or getattr(product, "sku", None)

            order_items_to_create.append(
                OrderItem(
                    order=order,
                    product_id=product.id,
                    variant_id=getattr(item, "variant_id", None),
                    product_name=product_title,
                    variant_name=variant_name,
                    sku=sku,
                    quantity=item.quantity,
                    unit_price=unit_price,
                    subtotal_price=unit_price * item.quantity,
                )
            )
        OrderItem.objects.bulk_create(order_items_to_create)

        # مدیریت پاک‌سازی سبد خرید
        # در صورت موفقیت آمیز بودن ثبت سفارش، سبد خرید مربوط به مهمان را از سشن حذف می‌کنیم
        if not request.user.is_authenticated and "cart_id" in request.session:
            del request.session["cart_id"]

        # هدایت کاربر به صفحه تایید سفارش
        return redirect(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": order.order_number},
            )
        )



    # مدیریت خطاها در سطح کلاس
    def dispatch(self, request, *args, **kwargs):
        # GET برای همه آزاد است تا صفحه checkout و فرم Inline OTP دیده شود
        if request.method == "GET":
            return super().dispatch(request, *args, **kwargs)

        # فقط درخواست‌های حساسِ غیر GET بدون احراز هویت را می‌بندیم
        if not request.user.is_authenticated:
            messages.warning(
                request,
                "لطفاً برای تکمیل خرید ابتدا شماره موبایل خود را تایید کنید.",
            )
            return redirect("cart:checkout")

        return super().dispatch(request, *args, **kwargs)
