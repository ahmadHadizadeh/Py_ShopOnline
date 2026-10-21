from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

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
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "داشبورد مدیریت فروشگاه")
        self.assertContains(response, "سفارش‌های آماده پردازش")
        self.assertContains(response, "محصولات کم‌موجودی")
        self.assertContains(response, "پرداخت‌های در انتظار")
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
