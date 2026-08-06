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


def checkout_view(request):
    return HttpResponse(request, "⭐ Add To Card Success | Ahmad ⭐")


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


class CheckoutView(LoginRequiredMixin, View):
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
                        # فرمت‌دهی هزینه برای نمایش در قالب (مثلاً با ',' برای جداکننده هزارگان)
                        # توجه: برای نمایش فارسی، ممکن است نیاز به کتابخانه جداگانه یا تنظیمات خاصی باشد
                        # اینجا از یک فرمت‌دهی ساده استفاده می‌کنیم که باید در قالب یا با جاوااسکریپت تنظیم شود
                        formatted_cost_display = f"{cost:,}".replace(
                            ",", "."
                        )  # ساده‌ترین فرمت فارسی

                        shipping_methods_with_costs.append(
                            {
                                "method": method,
                                "cost": cost,  # هزینه خام برای استفاده در جاوااسکریپت و محاسبات
                                "formatted_cost": formatted_cost_display,  # هزینه فرمت شده برای نمایش مستقیم
                            }
                        )
                    except Exception as e:
                        logger.error(
                            f"Error calculating shipping cost for method {method.id} with cart total {cart.total_price}: {e}"
                        )
                        # در صورت بروز خطا، هزینه را صفر در نظر می‌گیریم و لاگ می‌کنیم
                        shipping_methods_with_costs.append(
                            {
                                "method": method,
                                "cost": 0,
                                "formatted_cost": "0",
                            }
                        )
            except Exception as e:
                logger.error(f"Error fetching or processing shipping methods: {e}")
                # اگر خطای کلی در دسترسی به روش‌های ارسال رخ داد
                messages.error(
                    request,
                    "امکان محاسبه هزینه‌های ارسال وجود ندارد. لطفاً بعداً تلاش کنید.",
                )
                # در این حالت، بهتر است کاربر را به صفحه سبد خرید برگردانیم یا با خطای مناسب مواجه کنیم
                # return redirect("cart:detail") # یا روش دیگر مدیریت خطا

        # اگر آدرس فرم ارائه نشده بود (در متد POST)، فرم آدرس را بساز
        if address_form is None:
            address_form = self.get_address_form(request.user)

        # ساخت دیکشنری context برای ارسال به قالب
        return {
            "cart": cart,
            "form": address_form,  # فرم آدرس (مقداردهی شده یا خالی)
            "user_addresses": Address.objects.filter(user=request.user).order_by(
                "-is_default", "-id"  # مرتب‌سازی بر اساس پیش‌فرض بودن و سپس ID
            ),
            # ارسال لیست روش‌های ارسال به همراه هزینه محاسبه شده و فرمت شده
            "shipping_methods_data": shipping_methods_with_costs,
            "province_cities": PROVINCES_AND_CITIES,
        }

    def get(self, request):
        # دریافت سبد خرید کاربر با پیش‌بارگذاری آیتم‌ها و محصولات
        cart = (
            Cart.objects.filter(user=request.user)
            .prefetch_related("items__product")
            .first()
        )
        # اگر سبد خرید وجود نداشت یا خالی بود، پیام نمایش بده و به صفحه سبد خرید هدایت کن
        if not cart or not cart.items.exists():
            messages.warning(request, "سبد خرید شما خالی است.")
            return redirect("cart:detail")

        # نمایش صفحه چک‌اوت با داده‌های لازم
        return render(request, self.template_name, self.get_context_data(request, cart))

    @transaction.atomic  # اجرای کل متد POST در یک تراکنش اتمیک
    def post(self, request):
        cart = (
            Cart.objects.select_for_update()  # قفل کردن سبد خرید برای جلوگیری از تغییرات همزمان
            .filter(user=request.user)
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
            # در صورت عدم انتخاب روش ارسال، مجدداً صفحه چک‌اوت را با داده‌های فعلی نمایش بده
            return render(
                request, self.template_name, self.get_context_data(request, cart)
            )

        # دریافت روش ارسال بدون ایجاد خطای سراسری ۴۰۴
        shipping_method = ShippingMethod.objects.filter(
            id=shipping_method_id, is_active=True
        ).first()

        if not shipping_method:
            messages.error(request, "روش ارسال انتخاب شده معتبر نیست.")
            return render(
                request, self.template_name, self.get_context_data(request, cart)
            )

        # پردازش فرم آدرس
        address_id = request.POST.get("address_id")
        address_instance = None
        if address_id:
            # تلاش برای یافتن آدرس انتخاب شده از بین آدرس‌های ذخیره شده کاربر
            address_instance = Address.objects.filter(
                id=address_id,
                user=request.user,
            ).first()

        # ساخت فرم آدرس، با instance اگر آدرس ذخیره شده‌ای انتخاب شده باشد
        address_form = AddressForm(request.POST, instance=address_instance)

        # اعتبارسنجی فرم آدرس
        if not address_form.is_valid():
            # اگر فرم نامعتبر بود، صفحه را با خطاها مجدداً نمایش بده
            return render(
                request,
                self.template_name,
                self.get_context_data(request, cart, address_form),
            )

        # ذخیره یا به‌روزرسانی آدرس
        address = address_form.save(commit=False)  # ذخیره موقت برای تنظیم فیلدهای اضافی
        address.user = request.user  # اطمینان از اینکه آدرس به کاربر فعلی تعلق دارد
        address.save()  # ذخیره نهایی آدرس

        # اگر آدرس جدید به عنوان پیش‌فرض انتخاب شده، آدرس‌های قبلی کاربر را غیرفعال کن
        if address.is_default:
            Address.objects.filter(user=request.user).exclude(
                id=address.id  # به جز آدرس فعلی
            ).update(is_default=False)

        # بررسی موجودی و فعال بودن محصولات در سبد خرید
        for item in cart_items:
            product = item.product
            # استخراج نام محصول با اولویت‌بندی
            product_title = getattr(product, "name", None) or getattr(
                product, "title", "محصول"
            )

            if not product.is_active:
                messages.error(request, f"محصول '{product_title}' دیگر فعال نیست.")
                # بازگشت به صفحه چک‌اوت با نمایش خطا
                return render(
                    request,
                    self.template_name,
                    self.get_context_data(request, cart, address_form),
                )

            # بررسی موجودی در صورت داشتن فیلد stock
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

        # محاسبه هزینه ارسال با استفاده از متد مربوطه
        try:
            shipping_amount = shipping_method.calculate_shipping_cost(subtotal_amount)
            # اطمینان از اینکه هزینه ارسال عدد است
            if not isinstance(shipping_amount, (int, float, Decimal)):
                logger.error(
                    f"Shipping cost calculation returned non-numeric type: {type(shipping_amount)}"
                )
                shipping_amount = Decimal("0.00")  # مقدار پیش‌فرض در صورت خطا
        except Exception as e:
            logger.error(
                f"Error in calculate_shipping_cost for {shipping_method.id}: {e}"
            )
            shipping_amount = Decimal("0.00")  # مقدار پیش‌فرض در صورت خطا

        final_amount = subtotal_amount + shipping_amount

        # ایجاد سفارش (Order)
        order = Order.objects.create(
            user=request.user,
            cart=cart,  # اتصال سبد خرید به سفارش
            shipping_method=shipping_method,  # ذخیره روش ارسال انتخاب شده
            status=Order.Status.PENDING,  # وضعیت اولیه سفارش: در انتظار پرداخت
            subtotal_amount=subtotal_amount,
            shipping_amount=shipping_amount,
            final_amount=final_amount,
            # سایر فیلدهای مورد نیاز Order را اینجا اضافه کنید
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

        # ایجاد آیتم‌های سفارش (OrderItems) با استفاده از bulk_create برای بهینه‌سازی
        order_items_to_create = []
        for item in cart_items:
            product = item.product
            variant = getattr(item, "variant", None)  # دریافت واریانت در صورت وجود
            unit_price = product.price
            product_title = getattr(product, "name", None) or getattr(
                product, "title", ""
            )
            variant_name = getattr(variant, "name", "") if variant else ""
            sku = getattr(variant, "sku", None) or getattr(
                product, "sku", None
            )  # دریافت SKU

            order_items_to_create.append(
                OrderItem(
                    order=order,
                    product_id=product.id,
                    variant_id=getattr(
                        item, "variant_id", None
                    ),  # شناسه واریانت اگر وجود دارد
                    product_name=product_title,
                    variant_name=variant_name,
                    sku=sku,
                    quantity=item.quantity,
                    unit_price=unit_price,
                    subtotal_price=unit_price * item.quantity,
                )
            )
        OrderItem.objects.bulk_create(order_items_to_create)

        # حذف سبد خرید پس از ایجاد سفارش موفق
        cart.items.all().delete()  # حذف آیتم‌های سبد خرید
        cart.delete()  # حذف خود سبد خرید

        # هدایت کاربر به صفحه تایید سفارش
        return redirect(
            reverse(
                "orders:order_confirmation",
                kwargs={
                    "order_number": order.order_number
                },  # فرض بر وجود فیلد order_number در مدل Order
            )
        )

    # مدیریت خطاها در سطح کلاس
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Exception as e:
            logger.error(f"Unhandled exception in CheckoutView dispatch: {str(e)}")
            messages.error(
                request, "خطای سیستمی غیرمنتظره‌ای رخ داد. لطفاً مجدداً تلاش کنید."
            )
            # در صورت بروز خطای غیرمنتظره، کاربر را به صفحه سبد خرید هدایت می‌کنیم
            return redirect("cart:detail")
