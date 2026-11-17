from django.conf import settings


DEMO_CUSTOMER_USERNAME_PREFIX = "demo_customer_"


def exclude_demo_customer_records(queryset, *, user_lookup="user__username"):
    """Hide visitor-owned records from the shared demo manager account."""
    if not settings.DEMO_MODE:
        return queryset
    return queryset.exclude(
        **{f"{user_lookup}__startswith": DEMO_CUSTOMER_USERNAME_PREFIX}
    )


class DemoNoIndexMiddleware:
    """Prevent search engines from indexing any page or asset on the sales demo."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if settings.DEMO_MODE:
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        return response



def exclude_demo_visitor_order_movements(queryset):
    """Hide stock movement rows linked to private visitor-owned Demo orders."""
    if not settings.DEMO_MODE:
        return queryset

    from orders.models import Order

    visitor_order_numbers = Order.objects.filter(
        user__username__startswith=DEMO_CUSTOMER_USERNAME_PREFIX
    ).values("order_number")
    return queryset.exclude(order_number__in=visitor_order_numbers)
