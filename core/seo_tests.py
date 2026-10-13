from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models.category import Category
from catalog.models.product import Product


class SeoEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active_category = Category.objects.create(
            name="Public category",
            slug="public-category",
            is_active=True,
        )
        cls.inactive_category = Category.objects.create(
            name="Hidden category",
            slug="hidden-category",
            is_active=False,
        )

        cls.active_product = Product.objects.create(
            name="Public product",
            slug="public-product",
            is_active=True,
            category=cls.active_category,
        )
        cls.inactive_product = Product.objects.create(
            name="Hidden product",
            slug="hidden-product",
            is_active=False,
            category=cls.active_category,
        )

    def test_sitemap_lists_home_and_active_catalog_urls_only(self):
        response = self.client.get(
            reverse("sitemap"),
            secure=True,
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response["Content-Type"].startswith("application/xml")
        )

        content = response.content.decode()
        self.assertIn("https://codeamoz.com/", content)
        self.assertIn("/category/public-category/", content)
        self.assertIn("/product/public-product/", content)
        self.assertNotIn("hidden-category", content)
        self.assertNotIn("hidden-product", content)

    def test_robots_txt_is_plain_text_and_references_sitemap(self):
        response = self.client.get(
            reverse("robots_txt"),
            secure=True,
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response["Content-Type"].startswith("text/plain")
        )

        content = response.content.decode()
        self.assertIn("User-agent: *", content)
        self.assertIn("Disallow: /admin/", content)
        self.assertIn("Disallow: /cart/", content)
        self.assertIn(
            "Sitemap: https://codeamoz.com/sitemap.xml",
            content,
        )

# CANONICAL_SEO_TESTS_V1
@override_settings(
    ALLOWED_HOSTS=["testserver", "codeamoz.com", "www.codeamoz.com"]
)
class CanonicalMetadataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(
            name="SEO test category",
            slug="seo-test-category",
            is_active=True,
        )
        cls.product = Product.objects.create(
            name="SEO test product",
            slug="seo-test-product",
            is_active=True,
            category=cls.category,
        )

    def assert_canonical_metadata(self, response, path):
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        expected_url = f"https://codeamoz.com{path}"
        self.assertIn(
            f'<link rel="canonical" href="{expected_url}">',
            content,
        )
        self.assertIn(
            f'<meta property="og:url" content="{expected_url}">',
            content,
        )

    def test_homepage_uses_apex_canonical_on_www_host(self):
        response = self.client.get(
            reverse("catalog:product_list"),
            secure=True,
            HTTP_HOST="www.codeamoz.com",
        )
        self.assert_canonical_metadata(response, "/")

    def test_category_uses_its_own_apex_canonical_on_www_host(self):
        path = self.category.get_absolute_url()
        response = self.client.get(
            path,
            secure=True,
            HTTP_HOST="www.codeamoz.com",
        )
        self.assert_canonical_metadata(response, path)

    def test_product_uses_its_own_apex_canonical_on_www_host(self):
        path = self.product.get_absolute_url()
        response = self.client.get(
            path,
            secure=True,
            HTTP_HOST="www.codeamoz.com",
        )
        self.assert_canonical_metadata(response, path)

    def test_sitemap_uses_apex_urls_even_for_www_requests(self):
        response = self.client.get(
            reverse("sitemap"),
            secure=True,
            HTTP_HOST="www.codeamoz.com",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("https://codeamoz.com/", content)
        self.assertIn(
            f"https://codeamoz.com{self.category.get_absolute_url()}",
            content,
        )
        self.assertIn(
            f"https://codeamoz.com{self.product.get_absolute_url()}",
            content,
        )
        self.assertNotIn("https://www.codeamoz.com/", content)

    def test_robots_txt_uses_apex_sitemap_even_for_www_requests(self):
        response = self.client.get(
            reverse("robots_txt"),
            secure=True,
            HTTP_HOST="www.codeamoz.com",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            "Sitemap: https://codeamoz.com/sitemap.xml",
            content,
        )
        self.assertNotIn("Sitemap: https://www.codeamoz.com/", content)
