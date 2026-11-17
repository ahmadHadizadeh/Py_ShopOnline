"""Canonicalize case variants of known Django route literals.

Only application-defined, static route components are normalized. Dynamic
identifiers such as product/category slugs and order numbers are preserved.
Static/media paths and query strings are intentionally left untouched.
"""

import re

from django.http import HttpResponse
from django.utils.encoding import iri_to_uri


_CASE_INSENSITIVE = re.IGNORECASE


# Django's admin routes contain only fixed route names and numeric object IDs.
# Lowercase the complete admin path, but never touch its query string.
_ADMIN_PATH = re.compile(r"/admin(?:/(?P<tail>.*))?/?$", _CASE_INSENSITIVE)


def _route(pattern, builder):
    return re.compile(pattern, _CASE_INSENSITIVE), builder


# Keep these route shapes synchronized with core/urls.py and the included
# catalog/cart/accounts/orders URLconfs. Capture dynamic values separately.
_ROUTE_PATTERNS = (
    _route(r"/sitemap\.xml/?$", lambda match: "/sitemap.xml"),
    _route(r"/robots\.txt/?$", lambda match: "/robots.txt"),

    # Catalog routes. Slugs are database identifiers: preserve their exact case.
    _route(
        r"/product/(?P<slug>[-\w\u0600-\u06FF]+)/?$",
        lambda match: f"/product/{match.group('slug')}/",
    ),
    _route(
        r"/category/(?P<slug>[-\w\u0600-\u06FF]+)/?$",
        lambda match: f"/category/{match.group('slug')}/",
    ),
    _route(
        r"/wishlist/toggle/(?P<slug>[-\w\u0600-\u06FF]+)/?$",
        lambda match: f"/wishlist/toggle/{match.group('slug')}/",
    ),
    _route(
        r"/ajax/search-suggestions/?$",
        lambda match: "/ajax/search-suggestions/",
    ),

    # Cart routes. Their dynamic path parameters are numeric IDs.
    _route(r"/cart/?$", lambda match: "/cart/"),
    _route(
        r"/cart/add/(?P<product_id>\d+)/?$",
        lambda match: f"/cart/add/{match.group('product_id')}/",
    ),
    _route(
        r"/cart/items/(?P<item_id>\d+)/(?P<action>update|remove|save|move-to-cart)/?$",
        lambda match: (
            f"/cart/items/{match.group('item_id')}/"
            f"{match.group('action').lower()}/"
        ),
    ),
    _route(r"/cart/checkout/?$", lambda match: "/cart/checkout/"),

    # Accounts routes. Keep order numbers unchanged.
    _route(
        r"/accounts/ajax/(?P<action>send-otp|verify-otp|cities)/?$",
        lambda match: f"/accounts/ajax/{match.group('action').lower()}/",
    ),
    _route(
        r"/accounts/(?P<action>login|logout)/?$",
        lambda match: f"/accounts/{match.group('action').lower()}/",
    ),
    _route(r"/accounts/dashboard/?$", lambda match: "/accounts/dashboard/"),
    _route(
        r"/accounts/dashboard/orders/?$",
        lambda match: "/accounts/dashboard/orders/",
    ),
    _route(
        r"/accounts/dashboard/orders/(?P<order_number>[^/]+)/?$",
        lambda match: (
            f"/accounts/dashboard/orders/{match.group('order_number')}/"
        ),
    ),
    _route(
        r"/accounts/dashboard/addresses/?$",
        lambda match: "/accounts/dashboard/addresses/",
    ),
    _route(
        r"/accounts/dashboard/addresses/create/?$",
        lambda match: "/accounts/dashboard/addresses/create/",
    ),
    _route(
        r"/accounts/dashboard/addresses/(?P<pk>\d+)/(?P<action>update|delete)/?$",
        lambda match: (
            f"/accounts/dashboard/addresses/{match.group('pk')}/"
            f"{match.group('action').lower()}/"
        ),
    ),

    # Orders routes. Gateway names are stable lowercase identifiers;
    # order numbers are opaque and must retain their original case.
    _route(
        r"/orders/initiate-payment/(?P<order_number>[^/]+)/?$",
        lambda match: (
            f"/orders/initiate-payment/{match.group('order_number')}/"
        ),
    ),
    _route(
        r"/orders/payment/callback/?$",
        lambda match: "/orders/payment/callback/",
    ),
    _route(
        r"/orders/payment/callback/(?P<gateway_name>[^/]+)/?$",
        lambda match: (
            f"/orders/payment/callback/{match.group('gateway_name').lower()}/"
        ),
    ),
    _route(
        r"/orders/payment/(?P<result>success|failed)/(?P<order_number>[^/]+)/?$",
        lambda match: (
            f"/orders/payment/{match.group('result').lower()}/"
            f"{match.group('order_number')}/"
        ),
    ),
    _route(
        r"/orders/mock-payment-gateway/?$",
        lambda match: "/orders/mock-payment-gateway/",
    ),
    _route(
        r"/orders/order/confirm/(?P<order_number>[^/]+)/?$",
        lambda match: f"/orders/order/confirm/{match.group('order_number')}/",
    ),
)


def canonicalize_route_path(path):
    """Return the canonical path for a known route, otherwise None."""
    admin_match = _ADMIN_PATH.fullmatch(path)
    if admin_match:
        tail = (admin_match.group("tail") or "").strip("/")
        canonical_path = f"/admin/{tail.lower()}/" if tail else "/admin/"
        return canonical_path if canonical_path != path else None

    for pattern, builder in _ROUTE_PATTERNS:
        match = pattern.fullmatch(path)
        if match:
            canonical_path = builder(match)
            return canonical_path if canonical_path != path else None

    return None


class URLCaseNormalizationMiddleware:
    """Redirect case variants of known routes to their canonical lowercase form."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        canonical_path = canonicalize_route_path(request.path_info)
        if canonical_path is not None:
            query_string = request.META.get("QUERY_STRING", "")
            target = canonical_path
            if query_string:
                target = f"{target}?{query_string}"

            # 308 preserves the original HTTP method and request body.
            response = HttpResponse(status=308)
            response["Location"] = iri_to_uri(target)
            return response

        return self.get_response(request)
