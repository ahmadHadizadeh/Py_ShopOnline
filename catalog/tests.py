from decimal import Decimal
# فایل: catalog/tests.py
from django.test import TestCase, Client
from django.contrib import admin
from django.urls import reverse
from django.contrib.auth import get_user_model
from catalog.models.product import Product
from catalog.models.category import Category
from catalog.models.review import Review
from catalog.models.variant import ProductVariant
from catalog.services import save_product_review, toggle_wishlist

User = get_user_model()

class BaseTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.category = Category.objects.create(name="Cat", slug="cat")
        self.product1 = Product.objects.create(name="P1", slug="p1", is_active=True, category=self.category)
        self.client = Client()

class ReviewServiceTests(BaseTestCase):
    def test_save_review_unauthenticated_user(self):
        with self.assertRaisesMessage(ValueError, "برای ثبت نظر ابتدا وارد حساب کاربری شوید."):
            save_product_review(None, self.product1, 5, "test")

class WishlistServiceTests(BaseTestCase):
    def test_toggle_wishlist_unauthenticated_user(self):
        with self.assertRaisesMessage(ValueError, "کاربر احراز هویت نشده است."):
            toggle_wishlist(None, self.product1.pk)

class ProductDetailViewTests(BaseTestCase):
    def test_product_detail_renders_required_variant_selector(self):
        product = Product.objects.create(
            name="Variant Product",
            slug="variant-product",
            is_active=True,
            stock=10,
            category=self.category,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="قرمز",
            price_adjustment=150,
        )

        url = reverse(
            "catalog:product_detail",
            kwargs={"slug": product.slug},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-variant"')
        self.assertContains(response, 'name="variant_id"')
        self.assertContains(response, f'value="{variant.pk}"')
        self.assertContains(response, 'required')

    def test_product_detail_post_review_unauthenticated(self):
        url = reverse("catalog:product_detail", kwargs={"slug": self.product1.slug})
        response = self.client.post(url, {"rating": 5, "comment": "test"})
        # در اینجا چون 'login' ندارید، انتظار داریم خطای 403 یا 401 بدهد یا به صفحه اصلی برود
        # تست فقط صحت عدم ثبت نظر را چک می‌کند
        self.assertEqual(Review.objects.count(), 0)

class OtpCsrfIntegrationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Cat OTP", slug="cat-otp")
        self.product = Product.objects.create(
            name="OTP Product",
            slug="otp-product",
            is_active=True,
            category=self.category,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse("catalog:product_detail", kwargs={"slug": self.product.slug})

    def test_otp_page_bootstraps_csrf_cookie_and_send_otp_accepts_csrf(self):
        host = "otp-test.loca.lt"

        response = self.client.get(
            self.url,
            secure=True,
            HTTP_HOST=host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)

        csrf_token = response.cookies["csrftoken"].value
        otp_response = self.client.post(
            reverse("accounts:send_otp"),
            data='{"phone":"09123456789"}',
            content_type="application/json",
            secure=True,
            HTTP_HOST=host,
            HTTP_ORIGIN=f"https://{host}",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(otp_response.status_code, 200)
        self.assertEqual(otp_response.json().get("status"), "success")


class CategoryRootValidationRegressionTests(TestCase):
    def test_new_root_category_without_parent_validates(self):
        category = Category(name="Regression root", slug="regression-root")
        category.full_clean()
        self.assertIsNone(category.parent_id)



class ProductAdminUsabilityTests(TestCase):
    def test_product_fields_are_grouped_and_derived_status_is_read_only(self):
        product_admin = admin.site._registry[Product]

        self.assertEqual(
            tuple(product_admin.readonly_fields),
            ("is_available_status", "created", "updated"),
        )
        self.assertEqual(
            tuple(title for title, _options in product_admin.fieldsets),
            (
                "اطلاعات اصلی محصول",
                "قیمت و موجودی",
                "محتوای صفحه محصول",
                "ارسال و گارانتی",
                "تنظیمات تکمیلی",
            ),
        )

        configured_fields = {
            field
            for _title, options in product_admin.fieldsets
            for field in options["fields"]
        }
        self.assertSetEqual(
            configured_fields,
            {
                "name",
                "name_en",
                "brand",
                "category",
                "image",
                "price",
                "old_price",
                "stock",
                "is_active",
                "is_available_status",
                "description",
                "highlights",
                "review",
                "guarantee",
                "delivery_info",
                "slug",
                "related_products",
                "created",
                "updated",
            },
        )



class CategoryAdminDeletionSafetyTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="category-delete-safety-admin",
            email="category-delete-safety@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(self.admin_user)
        self.category = Category.objects.create(
            name="Category With Products",
            slug="category-with-products",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Protected Category Product",
            slug="protected-category-product",
            price=Decimal("120000"),
            stock=2,
            is_active=True,
        )

    def test_category_with_products_cannot_be_deleted_from_admin(self):
        response = self.client.post(
            reverse("admin:catalog_category_delete", args=[self.category.pk]),
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_empty_category_remains_deletable(self):
        empty_category = Category.objects.create(
            name="Empty Category",
            slug="empty-category",
        )

        response = self.client.post(
            reverse("admin:catalog_category_delete", args=[empty_category.pk]),
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=empty_category.pk).exists())
