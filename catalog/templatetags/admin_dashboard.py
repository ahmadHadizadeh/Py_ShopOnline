from django import template
from django.urls import reverse

from catalog.models.product import Product
from orders.models import Order, Payment


register = template.Library()
LOW_STOCK_THRESHOLD = 5


def _can_view_model(user, app_label: str, model_name: str) -> bool:
    if not user or not user.is_authenticated or not user.is_staff:
        return False
    return bool(
        user.is_superuser
        or user.has_perm(f"{app_label}.view_{model_name}")
        or user.has_perm(f"{app_label}.change_{model_name}")
    )


def _add_quick_link(
    user,
    links: list[dict],
    *,
    app_label: str,
    model_name: str,
    label: str,
    description: str,
    allow_add: bool = False,
    add_label: str = "افزودن مورد",
) -> None:
    can_view = _can_view_model(user, app_label, model_name)
    can_add = allow_add and bool(
        user
        and user.is_authenticated
        and user.is_staff
        and (
            user.is_superuser
            or user.has_perm(f"{app_label}.add_{model_name}")
        )
    )
    if not can_view and not can_add:
        return

    changelist_url = reverse(f"admin:{app_label}_{model_name}_changelist")
    add_url = reverse(f"admin:{app_label}_{model_name}_add") if can_add else None
    links.append(
        {
            "label": label,
            "description": description,
            "url": changelist_url if can_view else add_url,
            "add_url": add_url if can_view else None,
            "add_label": add_label,
        }
    )


@register.simple_tag
def store_admin_dashboard_data(user):
    """Return permission-aware operational metrics for the admin landing page."""
    can_view_orders = _can_view_model(user, "orders", "order")
    can_view_products = _can_view_model(user, "catalog", "product")
    can_view_payments = _can_view_model(user, "orders", "payment")

    data = {
        "can_view_orders": can_view_orders,
        "can_view_products": can_view_products,
        "can_view_payments": can_view_payments,
        "orders_to_process": 0,
        "orders_processing": 0,
        "active_products": 0,
        "low_stock_products": 0,
        "pending_payments": 0,
        "successful_payments_on_cancelled_orders": 0,
        "recent_orders": [],
        "quick_links": [],
    }

    if can_view_orders:
        order_queryset = Order.objects.all()
        data["orders_to_process"] = order_queryset.filter(
            status=Order.Status.PAID
        ).count()
        data["orders_processing"] = order_queryset.filter(
            status=Order.Status.PROCESSING
        ).count()
        data["recent_orders"] = order_queryset.select_related("user").order_by(
            "-created"
        )[:6]
        data["orders_url"] = reverse("admin:orders_order_changelist")

    if can_view_products:
        data["active_products"] = Product.objects.filter(is_active=True).count()
        data["low_stock_products"] = Product.objects.filter(
            is_active=True,
            is_available_status=True,
            stock__gt=0,
            stock__lte=LOW_STOCK_THRESHOLD,
        ).count()
        data["products_url"] = reverse("admin:catalog_product_changelist")
        data["low_stock_url"] = f"{data['products_url']}?availability=low_stock"

    if can_view_payments:
        data["pending_payments"] = Payment.objects.filter(
            status=Payment.Status.PENDING
        ).count()
        data["payments_url"] = reverse("admin:orders_payment_changelist")

    # A successful payment attached to a cancelled order is a reconciliation
    # exception: the provider result must be recorded, but the order must not
    # be fulfilled automatically. Only expose this metric when both models are
    # visible to the current staff user.
    if can_view_orders and can_view_payments:
        data["successful_payments_on_cancelled_orders"] = Payment.objects.filter(
            status=Payment.Status.SUCCESS,
            order__status=Order.Status.CANCELLED,
        ).count()
        data["successful_cancelled_payment_url"] = (
            f"{data['orders_url']}?status__exact=cancelled&payment__status__exact=success"
        )

    _add_quick_link(
        user, data["quick_links"], app_label="orders", model_name="order",
        label="سفارش‌ها",
        description="بررسی سفارش‌ها و پیگیری مراحل آماده‌سازی",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="catalog", model_name="product",
        label="محصولات",
        description="ثبت محصول، قیمت‌گذاری و کنترل موجودی",
        allow_add=True,
        add_label="افزودن محصول",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="catalog", model_name="category",
        label="دسته‌بندی‌ها",
        description="سازمان‌دهی محصولات فروشگاه",
        allow_add=True,
        add_label="افزودن دسته‌بندی",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="catalog", model_name="review",
        label="دیدگاه‌های مشتریان",
        description="بررسی و انتشار دیدگاه‌ها",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="orders", model_name="payment",
        label="پرداخت‌ها",
        description="بررسی وضعیت تراکنش‌ها و شناسه‌های پیگیری",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="orders", model_name="shippingmethod",
        label="روش‌های ارسال",
        description="مدیریت هزینه و زمان ارسال",
        allow_add=True,
        add_label="افزودن روش ارسال",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="cart", model_name="cart",
        label="سبدهای خرید",
        description="بررسی سبدهای فعال و رهاشده",
    )
    return data
