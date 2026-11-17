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
