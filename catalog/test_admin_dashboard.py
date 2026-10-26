from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.contrib import admin
from django.test import RequestFactory

from catalog.models.category import Category
from catalog.models.product import Product
from orders.models import Order, Payment

User = get_user_model()


class StoreAdminDashboardTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="store-dashboard-staff",
            password="TestPassword123!",
            is_staff=True,
        )
        self.category = Category.objects.create(
            name="Dashboard Category",
            slug="dashboard-category",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Dashboard Low Stock Product",
            slug="dashboard-low-stock-product",
            price=Decimal("125000"),
            stock=3,
            is_active=True,
        )
        self.order = Order.objects.create(
            user=self.staff,
            status=Order.Status.PAID,
            subtotal_amount=Decimal("125000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        self.pending_order = Order.objects.create(
            user=self.staff,
            status=Order.Status.PENDING,
            subtotal_amount=Decimal("80000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        Payment.objects.create(
            order=self.order,
            amount=self.order.final_amount,
            status=Payment.Status.SUCCESS,
            gateway_name="zarinpal",
            reference_code="DASHBOARD-REF-0001",
        )
        Payment.objects.create(
            order=self.pending_order,
            amount=self.pending_order.final_amount,
            status=Payment.Status.PENDING,
            gateway_name="zarinpal",
            transaction_code="DASHBOARD-TRX-0001",
        )

    def grant(self, user, app_label, codename):
        permission = Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )
        user.user_permissions.add(permission)

    def test_dashboard_shows_metrics_allowed_by_model_permissions(self):
        self.grant(self.staff, "orders", "view_order")
        self.grant(self.staff, "catalog", "view_product")
        self.grant(self.staff, "orders", "view_payment")
        failed_order = Order.objects.create(
            user=self.staff,
            status=Order.Status.PENDING,
            subtotal_amount=Decimal("45000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        Payment.objects.create(
            order=failed_order,
            amount=failed_order.final_amount,
            status=Payment.Status.FAILED,
            gateway_name="zarinpal",
            transaction_code="DASHBOARD-FAILED-TRX",
        )
        Product.objects.create(
            category=self.category,
            name="Dashboard Out Of Stock Product",
            slug="dashboard-out-of-stock-product",
            price=Decimal("25000"),
            stock=0,
            is_active=True,
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "داشبورد مدیریت فروشگاه")
        self.assertContains(response, "سفارش‌های آماده پردازش")
        self.assertContains(response, "محصولات کم‌موجودی")
        self.assertContains(response, "محصولات اتمام‌موجودی")
        self.assertContains(response, "availability=out_of_stock")
        self.assertContains(response, "پرداخت‌های در انتظار")
        self.assertContains(response, "پرداخت‌های ناموفق")
        self.assertContains(response, "status__exact=failed")
        self.assertContains(response, self.order.order_number)
        self.assertContains(response, "availability=low_stock")

    def test_staff_without_model_permissions_does_not_see_business_metrics(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "داشبورد مدیریت فروشگاه")
        self.assertNotContains(response, "سفارش‌های آماده پردازش")
        self.assertNotContains(response, "محصولات کم‌موجودی")
        self.assertNotContains(response, "پرداخت‌های در انتظار")
        self.assertNotContains(response, "پرداخت‌های موفق برای سفارش لغوشده")
        self.assertNotContains(response, self.order.order_number)

    def test_unavailable_filter_includes_inactive_product_with_positive_stock(self):
        available = Product.objects.create(
            category=self.category,
            name="Available Dashboard Product",
            slug="available-dashboard-product",
            price=Decimal("10000"),
            stock=4,
            is_active=True,
        )
        inactive = Product.objects.create(
            category=self.category,
            name="Inactive Dashboard Product",
            slug="inactive-dashboard-product",
            price=Decimal("10000"),
            stock=8,
            is_active=False,
        )
        admin_user = User.objects.create_superuser(
            username="store-dashboard-admin",
            email="admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("admin:catalog_product_changelist"),
            {"availability": "unavailable"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, inactive.name)
        self.assertNotContains(response, available.name)

    def test_low_stock_filter_excludes_out_of_stock_and_inactive_products(self):
        active_low_stock = self.product
        out_of_stock = Product.objects.create(
            category=self.category,
            name="Out Of Stock Dashboard Product",
            slug="out-of-stock-dashboard-product",
            price=Decimal("10000"),
            stock=0,
            is_active=True,
        )
        inactive_low_stock = Product.objects.create(
            category=self.category,
            name="Inactive Low Stock Dashboard Product",
            slug="inactive-low-stock-dashboard-product",
            price=Decimal("10000"),
            stock=2,
            is_active=False,
        )
        admin_user = User.objects.create_superuser(
            username="store-low-stock-admin",
            email="low-stock-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("admin:catalog_product_changelist"),
            {"availability": "low_stock"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, active_low_stock.name)
        self.assertNotContains(response, out_of_stock.name)
        self.assertNotContains(response, inactive_low_stock.name)

        out_of_stock_response = self.client.get(
            reverse("admin:catalog_product_changelist"),
            {"availability": "out_of_stock"},
        )

        self.assertEqual(out_of_stock_response.status_code, 200)
        self.assertContains(out_of_stock_response, out_of_stock.name)
        self.assertNotContains(out_of_stock_response, inactive_low_stock.name)

    def test_quick_links_only_offer_supported_add_actions(self):
        admin_user = User.objects.create_superuser(
            username="store-dashboard-actions-admin",
            email="actions-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "افزودن محصول")
        self.assertContains(response, "افزودن دسته‌بندی")
        self.assertContains(response, "افزودن روش ارسال")
        quick_links_html = (
            response.content.decode()
            .split('<div class="store-quick-links">', 1)[1]
            .split("</section>", 1)[0]
        )
        for forbidden_url in (
            "/admin/orders/order/add/",
            "/admin/orders/payment/add/",
            "/admin/catalog/review/add/",
            "/admin/cart/cart/add/",
        ):
            self.assertNotIn(f'href="{forbidden_url}"', quick_links_html)

    def test_change_only_permission_allows_order_dashboard_and_changelist(self):
        self.grant(self.staff, "orders", "change_order")
        self.client.force_login(self.staff)

        dashboard_response = self.client.get(reverse("admin:index"))

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "سفارش‌های آماده پردازش")
        self.assertContains(dashboard_response, self.order.order_number)
        self.assertNotContains(dashboard_response, "پرداخت‌های در انتظار")

        changelist_response = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(changelist_response.status_code, 200)

    def test_add_only_permission_shows_only_supported_product_add_link(self):
        self.grant(self.staff, "catalog", "add_product")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)

        quick_links_html = (
            response.content.decode()
            .split('<div class="store-quick-links">', 1)[1]
            .split("</section>", 1)[0]
        )

        # self.assertIn("افزودن محصول", quick_links_html)
        self.assertIn("محصولات", quick_links_html)

        self.assertIn(
            f'href="{reverse("admin:catalog_product_add")}"',
            quick_links_html,
        )
        self.assertNotIn(
            f'href="{reverse("admin:catalog_product_changelist")}"',
            quick_links_html,
        )
        self.assertNotContains(response, "محصولات کم‌موجودی")
        self.assertNotContains(response, self.order.order_number)

        add_response = self.client.get(reverse("admin:catalog_product_add"))
        self.assertEqual(add_response.status_code, 200)

    def test_staff_without_product_permission_cannot_open_product_changelist(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:catalog_product_changelist"))

        self.assertEqual(response.status_code, 403)

    def test_order_and_payment_add_pages_remain_forbidden(self):
        admin_user = User.objects.create_superuser(
            username="store-dashboard-no-add-admin",
            email="no-add-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        for url_name in (
            "admin:orders_order_add",
            "admin:orders_payment_add",
        ):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 403)

    def test_order_admin_exposes_only_registered_operational_actions(self):
        admin_user = User.objects.create_superuser(
            username="store-dashboard-actions-check",
            email="actions-check@example.test",
            password="TestPassword123!",
        )
        request = RequestFactory().get(reverse("admin:orders_order_changelist"))
        request.user = admin_user

        order_admin = admin.site._registry[Order]
        actions = order_admin.get_actions(request)

        expected_actions = {
            "move_paid_orders_to_processing",
            "move_processing_orders_to_completed",
            "cancel_unpaid_orders",
        }
        self.assertTrue(expected_actions.issubset(actions.keys()))

    def test_dashboard_flags_successful_payments_for_cancelled_orders(self):
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=["status"])

        # Explicitly create the state this dashboard metric is meant to detect.
        payment = Payment.objects.get(order=self.order)
        payment.status = Payment.Status.SUCCESS
        payment.reference_code = "DASHBOARD-CANCELLED-REF"
        payment.save(update_fields=["status", "reference_code"])
        self.grant(self.staff, "orders", "view_order")
        self.grant(self.staff, "orders", "view_payment")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.content.decode("utf-8"),
            r"پرداخت‌های موفق برای سفارش لغوشده</span>\s*<strong[^>]*>1</strong>",
        )
        self.assertContains(
            response,
            "status__exact=cancelled&amp;payment__status__exact=success",
        )