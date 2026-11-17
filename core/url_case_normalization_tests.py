from django.test import RequestFactory, TestCase, SimpleTestCase
from django.utils.encoding import iri_to_uri

from catalog.models.category import Category
from catalog.models.product import Product
from core.url_case_normalization import (
    URLCaseNormalizationMiddleware,
    canonicalize_route_path,
)


class CanonicalizeRoutePathTests(SimpleTestCase):
    def test_known_route_literals_are_normalized_without_changing_identifiers(self):
        cases = (
            ("/Admin", "/admin/"),
            ("/ADMIN/CATALOG/PRODUCT/42/CHANGE/", "/admin/catalog/product/42/change/"),
            ("/Product/مودم-روتر", "/product/مودم-روتر/"),
            ("/PRODUCT/Mixed-Case-Slug/", "/product/Mixed-Case-Slug/"),
            ("/Category/Mixed-Category/", "/category/Mixed-Category/"),
            ("/WISHLIST/Toggle/Mixed-Slug", "/wishlist/toggle/Mixed-Slug/"),
            ("/AJAX/Search-Suggestions", "/ajax/search-suggestions/"),
            ("/CART/ITEMS/27/MOVE-TO-CART", "/cart/items/27/move-to-cart/"),
            ("/ACCOUNTS/DASHBOARD/ORDERS/Order-AbC-2026", "/accounts/dashboard/orders/Order-AbC-2026/"),
            ("/ORDERS/PAYMENT/CALLBACK/ZarinPal", "/orders/payment/callback/zarinpal/"),
            ("/ORDERS/PAYMENT/SUCCESS/Order-AbC-2026", "/orders/payment/success/Order-AbC-2026/"),
        )

        for supplied_path, expected_path in cases:
            with self.subTest(supplied_path=supplied_path):
                self.assertEqual(
                    canonicalize_route_path(supplied_path),
                    expected_path,
                )

    def test_remaining_registered_routes_normalize_route_literals_only(self):
        """Cover route patterns that previously had no direct matrix case."""
        cases = (
            ("/SITEMAP.XML", "/sitemap.xml"),
            ("/ROBOTS.TXT", "/robots.txt"),
            ("/CART", "/cart/"),
            ("/CART/ADD/42", "/cart/add/42/"),
            ("/CART/ITEMS/27/UPDATE", "/cart/items/27/update/"),
            ("/CART/ITEMS/27/REMOVE", "/cart/items/27/remove/"),
            ("/CART/ITEMS/27/SAVE", "/cart/items/27/save/"),
            ("/CART/CHECKOUT", "/cart/checkout/"),
            ("/ACCOUNTS/AJAX/SEND-OTP", "/accounts/ajax/send-otp/"),
            ("/ACCOUNTS/AJAX/VERIFY-OTP", "/accounts/ajax/verify-otp/"),
            ("/ACCOUNTS/AJAX/CITIES", "/accounts/ajax/cities/"),
            ("/ACCOUNTS/LOGIN", "/accounts/login/"),
            ("/ACCOUNTS/LOGOUT", "/accounts/logout/"),
            ("/ACCOUNTS/DASHBOARD", "/accounts/dashboard/"),
            ("/ACCOUNTS/DASHBOARD/ORDERS", "/accounts/dashboard/orders/"),
            ("/ACCOUNTS/DASHBOARD/ADDRESSES", "/accounts/dashboard/addresses/"),
            ("/ACCOUNTS/DASHBOARD/ADDRESSES/CREATE", "/accounts/dashboard/addresses/create/"),
            ("/ACCOUNTS/DASHBOARD/ADDRESSES/42/UPDATE", "/accounts/dashboard/addresses/42/update/"),
            ("/ACCOUNTS/DASHBOARD/ADDRESSES/42/DELETE", "/accounts/dashboard/addresses/42/delete/"),
            ("/ORDERS/INITIATE-PAYMENT/Order-AbC-2026", "/orders/initiate-payment/Order-AbC-2026/"),
            ("/ORDERS/PAYMENT/CALLBACK", "/orders/payment/callback/"),
            ("/ORDERS/PAYMENT/FAILED/Order-AbC-2026", "/orders/payment/failed/Order-AbC-2026/"),
            ("/ORDERS/MOCK-PAYMENT-GATEWAY", "/orders/mock-payment-gateway/"),
            ("/ORDERS/ORDER/CONFIRM/Order-AbC-2026", "/orders/order/confirm/Order-AbC-2026/"),
        )

        for supplied_path, expected_path in cases:
            with self.subTest(supplied_path=supplied_path):
                self.assertEqual(
                    canonicalize_route_path(supplied_path),
                    expected_path,
                )
                self.assertIsNone(canonicalize_route_path(expected_path))

    def test_already_canonical_paths_are_not_redirected(self):
        canonical_paths = (
            "/",
            "/admin/",
            "/product/مودم-روتر/",
            "/category/network/",
            "/cart/",
            "/accounts/login/",
            "/orders/payment/callback/zarinpal/",
            "/sitemap.xml",
            "/robots.txt",
        )

        for path in canonical_paths:
            with self.subTest(path=path):
                self.assertIsNone(canonicalize_route_path(path))

    def test_unknown_static_and_media_paths_are_not_rewritten(self):
        excluded_paths = (
            "/Unknown/Path/",
            "/MEDIA/categories/2026/11/photo.webp",
            "/STATIC/CSS/style.css",
        )

        for path in excluded_paths:
            with self.subTest(path=path):
                self.assertIsNone(canonicalize_route_path(path))


class URLCaseNormalizationMiddlewareTests(SimpleTestCase):
    def test_redirect_preserves_query_string_and_post_method(self):
        request = RequestFactory().post(
            "/PRODUCT/Mixed-Case-Slug?Campaign=Summer&ReturnTo=%2FAdmin",
            data={"quantity": "2"},
        )
        middleware = URLCaseNormalizationMiddleware(
            lambda request: self.fail("Canonical paths must redirect before the view.")
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response["Location"],
            "/product/Mixed-Case-Slug/?Campaign=Summer&ReturnTo=%2FAdmin",
        )


class URLCaseNormalizationIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="URL case test",
            slug="url-case-test",
            is_active=True,
        )
        cls.product = Product.objects.create(
            name="Mixed case slug test product",
            slug="Mixed-Case-مودم",
            is_active=True,
            stock=1,
            category=cls.category,
        )

    def test_product_route_redirect_preserves_exact_database_slug(self):
        supplied_path = f"/PRODUCT/{self.product.slug}"
        expected_path = self.product.get_absolute_url()

        response = self.client.get(supplied_path)

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response["Location"], iri_to_uri(expected_path))

        canonical_response = self.client.get(expected_path)
        self.assertEqual(canonical_response.status_code, 200)

    def test_admin_without_slash_redirects_to_lowercase_canonical_path(self):
        response = self.client.get("/Admin")

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response["Location"], "/admin/")
