from django import template
from django.db.models import Count, Q, Sum
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



def _status_breakdown(queryset, choices: tuple) -> list[dict]:
    """Build a compact chart from the actual status values stored in the database."""
    counts = dict(
        queryset.order_by()
        .values("status")
        .annotate(total=Count("pk"))
        .values_list("status", "total")
    )
    total = sum(counts.values())
    if not total:
        return []

    return [
        {
            "key": key,
            "label": label,
            "count": counts.get(key, 0),
            "percentage": round(counts.get(key, 0) * 100 / total),
        }
        for key, label in choices
    ]


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
            "label": label if can_view else add_label,
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
        "can_view_order_readiness": can_view_orders and can_view_payments,
        "total_orders": 0,
        "orders_to_process": 0,
        "orders_requiring_reconciliation": 0,
        "orders_processing": 0,
        "active_products": 0,
        "low_stock_products": 0,
        "out_of_stock_products": 0,
        "pending_payments": 0,
        "failed_payments": 0,
        "confirmed_sales_amount": 0,
        "successful_payments_on_cancelled_orders": 0,
        "recent_orders": [],
        "order_status_breakdown": [],
        "payment_status_breakdown": [],
        "operational_alerts": [],
        "can_view_operational_alerts": (
            can_view_orders or can_view_products or can_view_payments
        ),
        "quick_links": [],
    }

    if can_view_orders:
        order_queryset = Order.objects.all()
        data["total_orders"] = order_queryset.count()
        data["orders_processing"] = order_queryset.filter(
            status=Order.Status.PROCESSING
        ).count()
        data["recent_orders"] = order_queryset.select_related("user").order_by(
            "-created"
        )[:6]
        data["orders_url"] = reverse("admin:orders_order_changelist")
        data["order_status_breakdown"] = _status_breakdown(
            order_queryset, Order.Status.choices
        )

    # Readiness is a payment-sensitive operational metric. Do not expose it
    # unless the staff member can view both orders and payments.
    if data["can_view_order_readiness"]:
        paid_orders = order_queryset.filter(status=Order.Status.PAID)
        data["ready_orders_url"] = (
            f"{data['orders_url']}?status__exact={Order.Status.PAID}"
            f"&stock_reduced__exact=1&payment__status__exact={Payment.Status.SUCCESS}"
        )
        data["orders_to_process"] = paid_orders.filter(
            stock_reduced=True,
            payment__status=Payment.Status.SUCCESS,
        ).count()
        data["orders_requiring_reconciliation"] = paid_orders.filter(
            Q(stock_reduced=False)
            | Q(payment__isnull=True)
            | ~Q(payment__status=Payment.Status.SUCCESS)
        ).count()

    if can_view_products:
        data["active_products"] = Product.objects.filter(is_active=True).count()
        data["low_stock_products"] = Product.objects.filter(
            is_active=True,
            is_available_status=True,
            stock__gt=0,
            stock__lte=LOW_STOCK_THRESHOLD,
        ).count()
        data["out_of_stock_products"] = Product.objects.filter(
            is_active=True,
            stock=0,
        ).count()
        data["products_url"] = reverse("admin:catalog_product_changelist")
        data["low_stock_url"] = f"{data['products_url']}?availability=low_stock"
        data["out_of_stock_url"] = f"{data['products_url']}?availability=out_of_stock"

    if can_view_payments:
        payment_queryset = Payment.objects.all()
        data["pending_payments"] = payment_queryset.filter(
            status=Payment.Status.PENDING
        ).count()
        data["failed_payments"] = payment_queryset.filter(
            status=Payment.Status.FAILED
        ).count()
        data["payments_url"] = reverse("admin:orders_payment_changelist")
        data["payment_status_breakdown"] = _status_breakdown(
            payment_queryset, Payment.Status.choices
        )

    # Show cumulative successful payment value only to staff allowed to view
    # both orders and payments. Cancelled/pending orders and refunded payments
    # are excluded; this is a paid-transaction total, not accounting net revenue.
    if can_view_orders and can_view_payments:
        data["confirmed_sales_amount"] = (
            Payment.objects.filter(
                status=Payment.Status.SUCCESS,
                order__status__in=(
                    Order.Status.PAID,
                    Order.Status.PROCESSING,
                    Order.Status.COMPLETED,
                ),
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )

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


    # Actionable alerts are derived only from metrics the current staff member
    # is allowed to see, and each one links directly to the relevant admin list.
    if can_view_orders and data["successful_payments_on_cancelled_orders"]:
        data["operational_alerts"].append(
            {
                "tone": "danger",
                "title": "پرداخت موفق برای سفارش لغوشده",
                "description": "وضعیت سفارش و نتیجه پرداخت باید با دقت بررسی شوند.",
                "count": data["successful_payments_on_cancelled_orders"],
                "url": data["successful_cancelled_payment_url"],
            }
        )

    if can_view_payments and data["failed_payments"]:
        data["operational_alerts"].append(
            {
                "tone": "danger",
                "title": "پرداخت‌های ناموفق",
                "description": "جزئیات تراکنش‌های ناموفق را بررسی کنید.",
                "count": data["failed_payments"],
                "url": f'{data["payments_url"]}?status__exact=failed',
            }
        )

    if data["can_view_order_readiness"] and data["orders_to_process"]:
        data["operational_alerts"].append(
            {
                "tone": "warning",
                "title": "سفارش‌های آماده پردازش",
                "description": "پرداخت موفق تأیید شده و کاهش موجودی ثبت شده است؛ سفارش منتظر شروع آماده‌سازی است.",
                "count": data["orders_to_process"],
                "url": data["ready_orders_url"],
            }
        )

    if (
        data["can_view_order_readiness"]
        and data["orders_requiring_reconciliation"]
    ):
        data["operational_alerts"].append(
            {
                "tone": "danger",
                "title": "سفارش‌های پرداخت‌شده نیازمند تطبیق",
                "description": "وضعیت پرداخت و ثبت کاهش موجودی سفارش با هم سازگار نیست؛ پیش از پردازش بررسی کنید.",
                "count": data["orders_requiring_reconciliation"],
                "url": f'{data["orders_url"]}?status__exact=paid',
            }
        )

    if can_view_payments and data["pending_payments"]:
        data["operational_alerts"].append(
            {
                "tone": "warning",
                "title": "پرداخت‌های در انتظار",
                "description": "وضعیت تراکنش‌ها را از فهرست پرداخت‌ها پیگیری کنید.",
                "count": data["pending_payments"],
                "url": f'{data["payments_url"]}?status__exact=pending',
            }
        )

    if can_view_products and data["out_of_stock_products"]:
        data["operational_alerts"].append(
            {
                "tone": "warning",
                "title": "محصولات اتمام‌موجودی",
                "description": "این محصولات فعال هستند اما موجودی قابل فروش ندارند.",
                "count": data["out_of_stock_products"],
                "url": data["out_of_stock_url"],
            }
        )

    if can_view_products and data["low_stock_products"]:
        data["operational_alerts"].append(
            {
                "tone": "info",
                "title": "محصولات کم‌موجودی",
                "description": "موجودی این محصولات به پنج عدد یا کمتر رسیده است.",
                "count": data["low_stock_products"],
                "url": data["low_stock_url"],
            }
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
        user, data["quick_links"], app_label="accounts", model_name="profile",
        label="پروفایل و تأیید مشتریان",
        description="بررسی وضعیت تأیید شماره همراه؛ کدهای تأیید در پنل نمایش داده نمی‌شوند",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="catalog", model_name="review",
        label="دیدگاه‌های مشتریان",
        description="بررسی و انتشار دیدگاه‌ها",
    )
    _add_quick_link(
        user, data["quick_links"], app_label="catalog", model_name="wishlist",
        label="علاقه‌مندی‌های مشتریان",
        description="مشاهده محصولاتی که مشتریان ذخیره کرده‌اند",
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