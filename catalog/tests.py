from decimal import Decimal
# فایل: catalog/tests.py
from django.test import TestCase, Client, RequestFactory
from django.db.models.deletion import ProtectedError
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import admin
from django.urls import reverse
from django.contrib.auth import get_user_model
from catalog.models.product import Product
from catalog.models.inventory import InventoryMovement
from catalog.models.category import Category
from catalog.models.review import Review
from catalog.models.variant import ProductVariant
from catalog.services import (
    adjust_product_stock,
    record_physical_stock_count,
    save_product_review,
    toggle_wishlist,
)

User = get_user_model()

class BaseTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.category = Category.objects.create(name="Cat", slug="cat")
        self.product1 = Product.objects.create(name="P1", slug="p1", is_active=True, category=self.category)
        self.client = Client()

class ProductCatalogSearchTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.searchable_product = Product.objects.create(
            name="لپ تاپ گیمینگ",
            name_en="Gaming Notebook",
            brand="Northwind",
            description="portable workstation",
            highlights="RTX graphics",
            stock=7,
            category=self.category,
        )

    def test_search_matches_existing_product_text_fields(self):
        for query in ("لپ تاپ", "Gaming", "Northwind", "portable workstation", "RTX graphics"):
            with self.subTest(query=query):
                response = self.client.get(reverse("catalog:product_list"), {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    list(response.context["products"].values_list("pk", flat=True)),
                    [self.searchable_product.pk],
                )
                self.assertEqual(response.context["search_query"], query)

    def test_search_excludes_inactive_products(self):
        Product.objects.create(
            name="Inactive Northwind Device",
            slug="inactive-northwind-device",
            is_active=False,
            stock=5,
            category=self.category,
        )
        response = self.client.get(reverse("catalog:product_list"), {"q": "Northwind"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["products"].values_list("pk", flat=True)),
            [self.searchable_product.pk],
        )

    def test_search_keeps_selected_category_filter(self):
        other_category = Category.objects.create(name="Other Category", slug="other-category")
        Product.objects.create(
            name="Northwind Other Device",
            slug="northwind-other-device",
            brand="Northwind",
            stock=3,
            category=other_category,
        )
        response = self.client.get(
            reverse("catalog:product_list_by_category", kwargs={"category_slug": self.category.slug}),
            {"q": "Northwind"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["products"].values_list("pk", flat=True)),
            [self.searchable_product.pk],
        )

    def test_search_trims_query_and_renders_header_controls(self):
        response = self.client.get(reverse("catalog:product_list"), {"q": "  Northwind  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["search_query"], "Northwind")
        self.assertContains(response, 'id="desktop-search-form"')
        self.assertContains(response, 'id="mobile-search-panel"')
        self.assertContains(response, 'id="mobile-navigation-panel"')
        self.assertContains(response, 'name="q"')

    def test_active_categories_are_available_in_header_on_product_detail(self):
        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": self.searchable_product.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.category.get_absolute_url())


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
            ("stock", "is_available_status", "created", "updated", "inventory_management_link"),
        )
        self.assertEqual(tuple(product_admin.list_editable), ())
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

    def test_product_admin_labels_and_help_text_are_persian_for_sellers(self):
        admin_user = User.objects.create_superuser(
            username="product-admin-labels",
            email="product-admin-labels@example.test",
            password="TestPassword123!",
        )
        category = Category.objects.create(
            name="Seller Product Category",
            slug="seller-product-category",
        )
        product = Product.objects.create(
            category=category,
            name="Seller Product",
            slug="seller-product",
            price=Decimal("100000"),
            stock=2,
        )
        self.client.force_login(admin_user)

        list_response = self.client.get(reverse("admin:catalog_product_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "دسته‌بندی")
        self.assertContains(list_response, category.name)
        self.assertNotContains(list_response, ">CATEGORY<")

        form_response = self.client.get(
            reverse("admin:catalog_product_change", args=[product.pk])
        )
        self.assertEqual(form_response.status_code, 200)
        self.assertContains(form_response, "دسته‌بندی")
        self.assertContains(form_response, "تصویر اصلی محصول")
        self.assertContains(form_response, "نشانی محصول")
        self.assertContains(form_response, "برای انتخاب چند محصول")
        self.assertContains(form_response, "مدیریت موجودی و ثبت شمارش فیزیکی")
        self.assertNotContains(form_response, ">Category<")
        self.assertNotContains(form_response, ">Image<")
        self.assertNotContains(form_response, ">Slug<")
        self.assertNotIn("stock", form_response.context["adminform"].form.fields)



class InventoryMovementAdminTests(TestCase):
    def test_inventory_movement_admin_is_read_only(self):
        admin_user = User.objects.create_superuser(
            username="inventory-audit-admin",
            email="inventory-audit-admin@example.test",
            password="TestPassword123!",
        )
        request = RequestFactory().get("/admin/catalog/inventorymovement/")
        request.user = admin_user
        movement_admin = admin.site._registry[InventoryMovement]

        self.assertTrue(movement_admin.has_view_permission(request))
        self.assertFalse(movement_admin.has_add_permission(request))
        self.assertFalse(movement_admin.has_change_permission(request))
        self.assertFalse(movement_admin.has_delete_permission(request))


class InventoryServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser(
            username="inventory-manager",
            email="inventory-manager@example.test",
            password="TestPassword123!",
        )
        self.category = Category.objects.create(
            name="Inventory Category",
            slug="inventory-category",
        )
        self.product = Product.objects.create(
            name="Inventory Product",
            slug="inventory-product",
            category=self.category,
            stock=5,
            is_active=True,
        )

    def test_relative_increase_and_decrease_record_auditable_movements(self):
        product, increase = adjust_product_stock(
            product_id=self.product.pk,
            delta=3,
            reason="دریافت محموله جدید",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 8)
        self.assertEqual(increase.quantity_before, 5)
        self.assertEqual(increase.quantity_after, 8)
        self.assertEqual(increase.delta, 3)
        self.assertEqual(increase.source, InventoryMovement.Source.MANUAL_ADJUSTMENT)
        self.assertEqual(increase.actor, self.manager)
        self.assertEqual(increase.actor_label, self.manager.get_username())

        product, decrease = adjust_product_stock(
            product_id=self.product.pk,
            delta=-2,
            reason="ثبت کالای آسیب‌دیده",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 6)
        self.assertEqual(decrease.quantity_before, 8)
        self.assertEqual(decrease.quantity_after, 6)
        self.assertEqual(decrease.delta, -2)

        self.assertEqual(
            InventoryMovement.objects.filter(product=self.product).count(),
            2,
        )

    def test_decrease_to_zero_updates_derived_availability(self):
        product, movement = adjust_product_stock(
            product_id=self.product.pk,
            delta=-5,
            reason="خروج کامل موجودی",
            actor=self.manager,
        )

        self.assertEqual(product.stock, 0)
        self.assertFalse(product.is_available_status)
        self.assertFalse(product.is_available)
        self.assertEqual(movement.quantity_after, 0)

    def test_adjustment_cannot_make_stock_negative_or_create_a_movement(self):
        with self.assertRaises(ValidationError):
            adjust_product_stock(
                product_id=self.product.pk,
                delta=-6,
                reason="کاهش بیش از موجودی",
                actor=self.manager,
            )

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_zero_manual_adjustment_and_empty_reason_are_rejected(self):
        for delta, reason in ((0, "عملیات صفر"), (1, "   ")):
            with self.subTest(delta=delta, reason=reason):
                with self.assertRaises(ValidationError):
                    adjust_product_stock(
                        product_id=self.product.pk,
                        delta=delta,
                        reason=reason,
                        actor=self.manager,
                    )

        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_physical_count_records_delta_and_zero_delta_count(self):
        product, correction = record_physical_stock_count(
            product_id=self.product.pk,
            physical_count=3,
            reason="شمارش پایان شیفت",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 3)
        self.assertEqual(correction.delta, -2)
        self.assertEqual(correction.source, InventoryMovement.Source.PHYSICAL_COUNT)

        product, confirmation = record_physical_stock_count(
            product_id=self.product.pk,
            physical_count=3,
            reason="تأیید شمارش مجدد",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 3)
        self.assertEqual(confirmation.delta, 0)
        self.assertEqual(confirmation.quantity_before, 3)
        self.assertEqual(confirmation.quantity_after, 3)
        self.assertEqual(InventoryMovement.objects.count(), 2)

    def test_physical_count_rejects_negative_or_non_integer_values(self):
        for physical_count in (-1, 1.5, True):
            with self.subTest(physical_count=physical_count):
                with self.assertRaises(ValidationError):
                    record_physical_stock_count(
                        product_id=self.product.pk,
                        physical_count=physical_count,
                        reason="شمارش فیزیکی",
                        actor=self.manager,
                    )

        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_stock_operations_require_product_change_permission(self):
        unprivileged_user = User.objects.create_user(
            username="inventory-no-permission",
            password="TestPassword123!",
        )

        with self.assertRaises(PermissionDenied):
            adjust_product_stock(
                product_id=self.product.pk,
                delta=1,
                reason="نباید اجرا شود",
                actor=unprivileged_user,
            )

        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_product_deactivation_remains_effective_after_stock_increase(self):
        inactive_product = Product.objects.create(
            name="Inactive Inventory Product",
            slug="inactive-inventory-product",
            category=self.category,
            stock=0,
            is_active=False,
        )

        product, _movement = adjust_product_stock(
            product_id=inactive_product.pk,
            delta=4,
            reason="ورود موجودی برای محصول غیرفعال",
            actor=self.manager,
        )

        self.assertEqual(product.stock, 4)
        self.assertFalse(product.is_available_status)
        self.assertFalse(product.is_available)



class InventoryAdminOperationViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser(
            username="inventory-page-manager",
            email="inventory-page-manager@example.test",
            password="TestPassword123!",
        )
        category = Category.objects.create(
            name="Inventory Page Category",
            slug="inventory-page-category",
        )
        self.product = Product.objects.create(
            name="Inventory Page Product",
            slug="inventory-page-product",
            category=category,
            stock=5,
            is_active=True,
        )
        self.client.force_login(self.manager)
        self.url = reverse(
            "admin:catalog_product_inventory",
            args=[self.product.pk],
        )

    def test_inventory_page_displays_operations_and_recent_history(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "موجودی ثبت‌شده در سامانه")
        self.assertContains(response, "افزایش موجودی (ورود کالا)")
        self.assertContains(response, "کاهش موجودی")
        self.assertContains(response, "ثبت شمارش فیزیکی")
        self.assertContains(response, "آخرین گردش‌های موجودی")

    def test_inventory_page_posts_relative_increase_and_shows_history(self):
        response = self.client.post(
            self.url,
            {
                "operation": "increase",
                "quantity": "4",
                "reason": "دریافت محموله تأمین‌کننده",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 9)

        movement = InventoryMovement.objects.get(product=self.product)
        self.assertEqual(movement.delta, 4)
        self.assertEqual(movement.quantity_before, 5)
        self.assertEqual(movement.quantity_after, 9)
        self.assertEqual(movement.actor, self.manager)

        history_response = self.client.get(self.url)
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "دریافت محموله تأمین‌کننده")

    def test_inventory_page_allows_zero_physical_count(self):
        response = self.client.post(
            self.url,
            {
                "operation": "physical_count",
                "quantity": "0",
                "reason": "شمارش قفسه و تأیید اتمام موجودی",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        movement = InventoryMovement.objects.get(product=self.product)
        self.assertEqual(movement.source, InventoryMovement.Source.PHYSICAL_COUNT)
        self.assertEqual(movement.delta, -5)

    def test_inventory_page_rejects_zero_relative_adjustment(self):
        response = self.client.post(
            self.url,
            {
                "operation": "decrease",
                "quantity": "0",
                "reason": "تعداد نامعتبر",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تعداد باید بیشتر از صفر باشد.")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_inventory_page_denies_staff_without_product_change_permission(self):
        read_only_staff = User.objects.create_user(
            username="inventory-read-only-staff",
            password="TestPassword123!",
            is_staff=True,
        )
        self.client.force_login(read_only_staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)



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

    def test_category_with_products_cannot_be_bulk_deleted_from_admin(self):
        response = self.client.post(
            reverse("admin:catalog_category_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(self.category.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())



class ProductCategoryModelProtectionTests(TestCase):
    def test_category_with_products_is_protected_outside_admin(self):
        category = Category.objects.create(
            name="ORM Protected Category",
            slug="orm-protected-category",
        )
        product = Product.objects.create(
            category=category,
            name="ORM Protected Product",
            slug="orm-protected-product",
            price=Decimal("1000"),
            stock=1,
            is_active=True,
        )

        with self.assertRaises(ProtectedError):
            category.delete()

        self.assertTrue(Category.objects.filter(pk=category.pk).exists())
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
