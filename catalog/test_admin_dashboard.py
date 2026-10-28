from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.contrib import admin
from django.test import RequestFactory

from accounts.models.address import Address
from accounts.models.profile import Profile
from cart.models import Cart

from catalog.models.category import Category
from catalog.models.product import Product
from catalog.models.wishlist import Wishlist
from catalog.templatetags.admin_dashboard import store_admin_dashboard_data
from orders.models import Order, OrderAddressSnapshot, Payment

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

    def test_dashboard_shows_total_order_count_and_all_orders_link(self):
        self.grant(self.staff, "orders", "view_order")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "کل سفارش‌ها")
        dashboard = store_admin_dashboard_data(self.staff)
        self.assertEqual(dashboard["total_orders"], 2)
        self.assertContains(
            response,
            f'href="{reverse("admin:orders_order_changelist")}"',
        )

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

        self.assertIn("افزودن محصول", quick_links_html)

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
            r"پرداخت‌های موفق برای سفارش لغوشده</span></div>\s*<strong[^>]*>1</strong>",
        )
        self.assertContains(
            response,
            "status__exact=cancelled&amp;payment__status__exact=success",
        )

    def test_order_changelist_shows_and_searches_snapshot_recipient_details(self):
        admin_user = User.objects.create_superuser(
            username="order-recipient-search-admin",
            email="recipient-search-admin@example.test",
            password="TestPassword123!",
        )
        OrderAddressSnapshot.objects.create(
            order=self.order,
            recipient_name="مریم رضایی",
            recipient_mobile="09125550123",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="خیابان نمونه، پلاک ۱۲",
        )
        self.client.force_login(admin_user)

        changelist_url = reverse("admin:orders_order_changelist")
        response = self.client.get(changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مریم رضایی")
        self.assertContains(response, "09125550123")

        search_response = self.client.get(changelist_url, {"q": "09125550123"})

        self.assertEqual(search_response.status_code, 200)
        self.assertContains(search_response, self.order.order_number)
        self.assertContains(search_response, "مریم رضایی")
        self.assertNotContains(search_response, self.pending_order.order_number)


    def test_persian_sidebar_exposes_real_admin_sections_and_registered_routes(self):
        admin_user = User.objects.create_superuser(
            username="seller-sidebar-admin",
            email="seller-sidebar-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html lang="fa-ir" dir="rtl">')
        self.assertContains(response, "شروع")
        self.assertContains(response, 'id="nav-sidebar"')
        self.assertContains(response, "جست‌وجو در منوها")
        self.assertContains(response, "محصولات و محتوا")
        self.assertContains(response, "سبدهای خرید")
        self.assertContains(response, "فروش و سفارش‌ها")
        self.assertContains(response, "فروش و سفارش‌ها")
        self.assertContains(response, "محصولات و موجودی")
        self.assertContains(response, "مشتریان و کاربران")
        self.assertContains(response, "ارسال و تحویل")
        self.assertContains(
            response,
            f'href="{reverse("admin:orders_order_changelist")}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("admin:catalog_product_changelist")}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("admin:accounts_address_changelist")}"',
        )

    def test_sidebar_hides_model_links_without_matching_permissions(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "نمای کلی فروشگاه")
        self.assertNotContains(
            response,
            f'href="{reverse("admin:orders_order_changelist")}"',
        )
        self.assertNotContains(
            response,
            f'href="{reverse("admin:catalog_product_changelist")}"',
        )

    def test_sidebar_only_shows_business_links_allowed_for_staff_user(self):
        self.grant(self.staff, "catalog", "view_product")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("admin:catalog_product_changelist")}"',
        )
        self.assertNotContains(
            response,
            f'href="{reverse("admin:orders_order_changelist")}"',
        )
        self.assertNotContains(
            response,
            f'href="{reverse("admin:orders_payment_changelist")}"',
        )

    def test_dashboard_charts_and_attention_items_use_real_status_counts(self):
        self.grant(self.staff, "orders", "view_order")
        self.grant(self.staff, "orders", "view_payment")
        self.grant(self.staff, "catalog", "view_product")
        self.client.force_login(self.staff)

        dashboard = store_admin_dashboard_data(self.staff)
        order_counts = {
            item["key"]: item["count"]
            for item in dashboard["order_status_breakdown"]
        }
        payment_counts = {
            item["key"]: item["count"]
            for item in dashboard["payment_status_breakdown"]
        }

        self.assertEqual(order_counts[Order.Status.PAID], 1)
        self.assertEqual(order_counts[Order.Status.PENDING], 1)
        self.assertEqual(payment_counts[Payment.Status.SUCCESS], 1)
        self.assertEqual(payment_counts[Payment.Status.PENDING], 1)
        self.assertEqual(
            sum(item["count"] for item in dashboard["order_status_breakdown"]),
            2,
        )
        self.assertEqual(
            sum(item["count"] for item in dashboard["payment_status_breakdown"]),
            2,
        )

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ترکیب وضعیت سفارش‌ها")
        self.assertContains(response, "ترکیب وضعیت پرداخت‌ها")
        self.assertContains(response, "موارد نیازمند رسیدگی")
        self.assertContains(response, "سفارش‌های آماده پردازش")
        self.assertContains(response, "پرداخت‌های در انتظار")
        self.assertContains(response, "محصولات کم‌موجودی")
        self.assertContains(response, "status__exact=paid")
        self.assertContains(response, "status__exact=pending")

    def test_product_and_category_lists_surface_business_controls(self):
        admin_user = User.objects.create_superuser(
            username="catalog-operations-admin",
            email="catalog-operations-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        product_response = self.client.get(reverse("admin:catalog_product_changelist"))
        self.assertEqual(product_response.status_code, 200)
        self.assertContains(product_response, "وضعیت فروش")
        self.assertContains(product_response, "قابل فروش")
        self.assertContains(product_response, "کم‌موجودی")

        product_admin = admin.site._registry[Product]
        self.assertIn("brand", product_admin.search_fields)
        self.assertIn("is_active", product_admin.list_filter)
        self.assertIn("availability_label", product_admin.list_display)
        self.assertIn("stock", product_admin.list_editable)
        self.assertEqual(product_admin.list_per_page, 25)

        category_response = self.client.get(
            reverse("admin:catalog_category_changelist")
        )
        self.assertEqual(category_response.status_code, 200)
        category_admin = admin.site._registry[Category]
        self.assertIn("parent", category_admin.list_display)
        self.assertIn("is_active", category_admin.list_display)
        self.assertIn("is_active", category_admin.list_filter)
        self.assertIn("parent", category_admin.list_filter)
        self.assertContains(category_response, "مادر")
        self.assertContains(category_response, "فعال")

    def test_order_and_payment_lists_show_readable_status_badges(self):
        admin_user = User.objects.create_superuser(
            username="order-status-badges-admin",
            email="order-status-badges-admin@example.test",
            password="TestPassword123!",
        )
        Order.objects.create(
            user=admin_user,
            status=Order.Status.PENDING,
            subtotal_amount=Decimal("50000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        self.client.force_login(admin_user)

        order_response = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(order_response.status_code, 200)
        self.assertContains(order_response, "store-status-pill--order-paid")
        self.assertContains(order_response, "store-status-pill--payment-success")
        self.assertContains(order_response, "پرداخت شده")
        self.assertContains(order_response, "پرداخت موفق")
        self.assertContains(order_response, "بدون پرداخت")

        payment_response = self.client.get(
            reverse("admin:orders_payment_changelist")
        )
        self.assertEqual(payment_response.status_code, 200)
        self.assertContains(payment_response, "store-status-pill--payment-success")

        payment_admin = admin.site._registry[Payment]
        self.assertIn("status_badge", payment_admin.list_display)
        self.assertEqual(payment_admin.list_per_page, 25)

    def test_cart_and_address_lists_prioritize_human_searchable_details(self):
        admin_user = User.objects.create_superuser(
            username="customer-operations-admin",
            email="customer-operations-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        guest_cart = Cart.objects.create(
            session_key="guest-cart-session-test",
            status=Cart.STATUS_ABANDONED,
        )
        cart_response = self.client.get(reverse("admin:cart_cart_changelist"))
        self.assertEqual(cart_response.status_code, 200)
        self.assertContains(cart_response, "مهمان")
        self.assertNotContains(cart_response, "guest-cart-session-test")

        cart_admin = admin.site._registry[Cart]
        self.assertNotIn("session_key", cart_admin.list_display)
        self.assertIn("session_key", cart_admin.search_fields)
        self.assertIn("user_display", cart_admin.list_display)
        self.assertEqual(cart_admin.list_per_page, 25)
        self.assertEqual(
            cart_admin.total_price_display.short_description,
            "جمع اقلام فعال (تومان)",
        )

        address_admin = admin.site._registry[Address]
        self.assertIn("province", address_admin.list_display)
        self.assertIn("phone_number", address_admin.search_fields)
        self.assertIn("user__username", address_admin.search_fields)

    def test_all_store_admin_changelists_render_for_authorized_admin(self):
        admin_user = User.objects.create_superuser(
            username="admin-changelist-smoke-test",
            email="admin-changelist-smoke-test@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        changelist_names = (
            "admin:catalog_product_changelist",
            "admin:catalog_category_changelist",
            "admin:catalog_productvariant_changelist",
            "admin:catalog_review_changelist",
            "admin:orders_order_changelist",
            "admin:orders_orderitem_changelist",
            "admin:orders_payment_changelist",
            "admin:orders_orderaddresssnapshot_changelist",
            "admin:orders_shippingmethod_changelist",
            "admin:cart_cart_changelist",
            "admin:cart_cartitem_changelist",
            "admin:accounts_address_changelist",
            "admin:accounts_profile_changelist",
        )

        for url_name in changelist_names:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200, url_name)

    def test_operational_filter_links_open_the_expected_filtered_lists(self):
        admin_user = User.objects.create_superuser(
            username="operational-filter-links-admin",
            email="operational-filter-links-admin@example.test",
            password="TestPassword123!",
        )
        out_of_stock_product = Product.objects.create(
            category=self.category,
            name="Dashboard Out Of Stock Product",
            slug="dashboard-out-of-stock-product",
            price=Decimal("90000"),
            stock=0,
            is_active=True,
        )
        cancelled_order = Order.objects.create(
            user=admin_user,
            status=Order.Status.CANCELLED,
            subtotal_amount=Decimal("40000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        Payment.objects.create(
            order=cancelled_order,
            amount=cancelled_order.final_amount,
            status=Payment.Status.SUCCESS,
            gateway_name="zarinpal",
            reference_code="CANCELLED-ORDER-ADMIN-FILTER-REF",
        )
        self.client.force_login(admin_user)

        low_stock_response = self.client.get(
            reverse("admin:catalog_product_changelist"),
            {"availability": "low_stock"},
        )
        self.assertEqual(low_stock_response.status_code, 200)
        self.assertContains(low_stock_response, self.product.name)
        self.assertNotContains(low_stock_response, out_of_stock_product.name)

        out_of_stock_response = self.client.get(
            reverse("admin:catalog_product_changelist"),
            {"availability": "out_of_stock"},
        )
        self.assertEqual(out_of_stock_response.status_code, 200)
        self.assertContains(out_of_stock_response, out_of_stock_product.name)
        self.assertNotContains(out_of_stock_response, self.product.name)

        cancelled_success_response = self.client.get(
            reverse("admin:orders_order_changelist"),
            {
                "status__exact": Order.Status.CANCELLED,
                "payment__status__exact": Payment.Status.SUCCESS,
            },
        )
        self.assertEqual(cancelled_success_response.status_code, 200)
        self.assertContains(cancelled_success_response, cancelled_order.order_number)
        self.assertNotContains(cancelled_success_response, self.order.order_number)

        successful_payments_response = self.client.get(
            reverse("admin:orders_payment_changelist"),
            {"status__exact": Payment.Status.SUCCESS},
        )
        self.assertEqual(successful_payments_response.status_code, 200)
        self.assertContains(successful_payments_response, "CANCELLED-ORDER-ADMIN-FILTER-REF")
        self.assertNotContains(successful_payments_response, "DASHBOARD-TRX-0001")

    def test_primary_admin_change_forms_render_for_authorized_admin(self):
        admin_user = User.objects.create_superuser(
            username="admin-change-form-smoke-test",
            email="admin-change-form-smoke-test@example.test",
            password="TestPassword123!",
        )
        cart = Cart.objects.create(user=self.staff, status=Cart.STATUS_ACTIVE)
        profile, _created = Profile.objects.get_or_create(user=self.staff)
        payment = Payment.objects.get(order=self.order)
        self.client.force_login(admin_user)

        change_urls = (
            ("admin:catalog_product_change", self.product.pk),
            ("admin:catalog_category_change", self.category.pk),
            ("admin:orders_order_change", self.order.pk),
            ("admin:orders_payment_change", payment.pk),
            ("admin:cart_cart_change", cart.pk),
            ("admin:accounts_profile_change", profile.pk),
        )
        for url_name, object_id in change_urls:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name, args=[object_id]))
                self.assertEqual(response.status_code, 200, url_name)

        cart_item_add_response = self.client.get(
            reverse("admin:cart_cartitem_add")
        )
        self.assertEqual(cart_item_add_response.status_code, 200)
        self.assertContains(cart_item_add_response, "جمع ردیف")
    def test_profile_admin_shows_customer_verification_but_never_otp_secrets(self):
        admin_user = User.objects.create_superuser(
            username="profile-privacy-admin",
            email="profile-privacy-admin@example.test",
            password="TestPassword123!",
        )
        profile, _created = Profile.objects.get_or_create(user=self.staff)
        profile.phone_number = "09127770001"
        profile.is_verified = False
        profile.otp_code = "731642"
        profile.save(update_fields=["phone_number", "is_verified", "otp_code", "updated_at"])

        self.client.force_login(admin_user)
        list_response = self.client.get(reverse("admin:accounts_profile_changelist"))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "09127770001")
        self.assertContains(list_response, "تأیید نشده")
        self.assertNotContains(list_response, "731642")
        self.assertNotContains(list_response, "otp_code")
        self.assertNotContains(list_response, "otp_created_at")

        change_response = self.client.get(
            reverse("admin:accounts_profile_change", args=[profile.pk])
        )
        self.assertEqual(change_response.status_code, 200)
        self.assertContains(change_response, "09127770001")
        self.assertNotContains(change_response, "731642")
        self.assertNotContains(change_response, "otp_code")
        self.assertNotContains(change_response, "otp_created_at")

        profile_admin = admin.site._registry[Profile]
        request = RequestFactory().get(
            reverse("admin:accounts_profile_change", args=[profile.pk])
        )
        request.user = admin_user
        visible_fields = set(profile_admin.get_fields(request, profile))
        self.assertIn("phone_number", visible_fields)
        self.assertIn("is_verified", visible_fields)
        self.assertNotIn("otp_code", visible_fields)
        self.assertNotIn("otp_created_at", visible_fields)
        self.assertFalse(profile_admin.has_add_permission(request))
        self.assertFalse(profile_admin.has_delete_permission(request, profile))

    def test_profile_admin_navigation_requires_profile_view_permission(self):
        self.client.force_login(self.staff)
        response_without_permission = self.client.get(reverse("admin:index"))
        self.assertNotContains(response_without_permission, "پروفایل و تأیید مشتریان")

        forbidden_profile_list = self.client.get(
            reverse("admin:accounts_profile_changelist")
        )
        self.assertEqual(forbidden_profile_list.status_code, 403)

        self.grant(self.staff, "accounts", "view_profile")
        response_with_permission = self.client.get(reverse("admin:index"))
        self.assertContains(response_with_permission, "پروفایل و تأیید مشتریان")

        profile_list = self.client.get(reverse("admin:accounts_profile_changelist"))
        self.assertEqual(profile_list.status_code, 200)

    def test_sidebar_search_loads_filter_script_and_accessible_empty_state(self):
        admin_user = User.objects.create_superuser(
            username="sidebar-search-admin",
            email="sidebar-search-admin@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="nav-filter"')
        self.assertContains(response, 'aria-controls="store-sidebar-menu"')
        self.assertContains(response, 'id="store-sidebar-menu"')
        self.assertContains(response, 'id="nav-filter-no-results"')
        self.assertContains(response, 'role="status"')
        self.assertContains(response, '/static/js/admin_sidebar.js')

    def test_sidebar_shows_add_only_variant_link_when_product_lookup_is_allowed(self):
        self.grant(self.staff, "catalog", "add_productvariant")
        self.grant(self.staff, "catalog", "view_product")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("admin:catalog_productvariant_add")}"',
        )
        self.assertNotContains(
            response,
            f'href="{reverse("admin:catalog_productvariant_changelist")}"',
        )
        add_response = self.client.get(
            reverse("admin:catalog_productvariant_add")
        )
        self.assertEqual(add_response.status_code, 200)


    def test_wishlist_admin_is_permission_gated_searchable_and_read_only(self):
        wishlist = Wishlist.objects.create(user=self.staff)
        wishlist.products.add(self.product)
        self.client.force_login(self.staff)

        dashboard_without_permission = self.client.get(reverse("admin:index"))
        self.assertNotContains(
            dashboard_without_permission,
            "علاقه‌مندی‌های مشتریان",
        )
        denied_list = self.client.get(reverse("admin:catalog_wishlist_changelist"))
        self.assertEqual(denied_list.status_code, 403)

        self.grant(self.staff, "catalog", "view_wishlist")
        dashboard_with_permission = self.client.get(reverse("admin:index"))
        self.assertEqual(dashboard_with_permission.status_code, 200)
        self.assertContains(dashboard_with_permission, "علاقه‌مندی‌های مشتریان")
        self.assertContains(
            dashboard_with_permission,
            reverse("admin:catalog_wishlist_changelist"),
        )

        list_response = self.client.get(reverse("admin:catalog_wishlist_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.staff.username)
        self.assertContains(list_response, self.product.name)

        change_response = self.client.get(
            reverse("admin:catalog_wishlist_change", args=[wishlist.pk])
        )
        self.assertEqual(change_response.status_code, 200)
        self.assertContains(change_response, self.product.name)

        wishlist_admin = admin.site._registry[Wishlist]
        request = RequestFactory().get(
            reverse("admin:catalog_wishlist_change", args=[wishlist.pk])
        )
        request.user = self.staff
        self.assertFalse(wishlist_admin.has_add_permission(request))
        self.assertFalse(wishlist_admin.has_change_permission(request, wishlist))
        self.assertFalse(wishlist_admin.has_delete_permission(request, wishlist))
