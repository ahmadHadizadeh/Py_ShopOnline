from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from catalog.models import Category, Product, Review
from catalog.templatetags.admin_dashboard import store_admin_dashboard_data
from orders.models import (
    Order,
    OrderAddressSnapshot,
    OrderItem,
    Payment,
)


User = get_user_model()


@override_settings(DEMO_MODE=True)
class DemoAdminPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_superuser(
            username="demo-manager",
            email="manager@example.invalid",
            password="ManagerOnlyTestPassword123!",
        )
        cls.visitor = User.objects.create_user(
            username="demo_customer_private",
            password=None,
        )
        cls.sample_buyer = User.objects.create_user(
            username="sample_buyer_1",
            password=None,
        )
        category = Category.objects.create(
            name="دسته آزمایشی",
            slug="demo-privacy-category",
        )
        product = Product.objects.create(
            name="محصول آزمایشی",
            slug="demo-privacy-product",
            category=category,
            price=Decimal("10000"),
            stock=10,
            is_active=True,
        )

        cls.visitor_order = cls._create_order(
            user=cls.visitor,
            product=product,
            suffix="VISITOR",
        )
        cls.sample_order = cls._create_order(
            user=cls.sample_buyer,
            product=product,
            suffix="SAMPLE",
        )
        Review.objects.create(
            product=product,
            user=cls.visitor,
            rating=5,
            comment="نظر خصوصی بازدیدکننده",
            is_approved=True,
        )
        Review.objects.create(
            product=product,
            user=cls.sample_buyer,
            rating=5,
            comment="نظر نمونه فروشگاه",
            is_approved=True,
        )

    @classmethod
    def _create_order(cls, *, user, product, suffix):
        order = Order.objects.create(
            user=user,
            status=Order.Status.PENDING,
            subtotal_amount=Decimal("10000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        OrderAddressSnapshot.objects.create(
            order=order,
            recipient_name="مشتری نمونه",
            recipient_mobile="00000000000",
            postal_code="0000000000",
            province="تهران",
            city="تهران",
            address_line="نشانی آزمایشی",
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            unit_price=Decimal("10000"),
            subtotal_price=Decimal("10000"),
        )
        Payment.objects.create(
            order=order,
            user=user,
            amount=Decimal("10000"),
            status=Payment.Status.PENDING,
            gateway_name="mock_gateway",
            transaction_code=f"DEMO-PRIVACY-{suffix}",
        )
        return order

    def setUp(self):
        self.request = RequestFactory().get("/management/")
        self.request.user = self.manager

    def _visible_ids(self, model):
        model_admin = admin.site._registry[model]
        return set(
            model_admin.get_queryset(self.request).values_list("pk", flat=True)
        )

    def test_admin_changelists_hide_every_visitor_owned_record(self):
        self.assertEqual(self._visible_ids(Order), {self.sample_order.pk})
        self.assertEqual(
            self._visible_ids(OrderItem),
            set(
                OrderItem.objects.filter(order=self.sample_order).values_list(
                    "pk", flat=True
                )
            ),
        )
        self.assertEqual(
            self._visible_ids(Payment),
            set(
                Payment.objects.filter(order=self.sample_order).values_list(
                    "pk", flat=True
                )
            ),
        )
        self.assertEqual(
            self._visible_ids(OrderAddressSnapshot),
            set(
                OrderAddressSnapshot.objects.filter(order=self.sample_order).values_list(
                    "pk", flat=True
                )
            ),
        )
        self.assertEqual(
            self._visible_ids(Review),
            set(Review.objects.filter(user=self.sample_buyer).values_list("pk", flat=True)),
        )

    def test_dashboard_metrics_exclude_visitor_orders_and_payments(self):
        dashboard = store_admin_dashboard_data(self.manager)

        self.assertEqual(dashboard["total_orders"], 1)
        self.assertEqual(dashboard["pending_payments"], 1)
        self.assertEqual(
            [order.pk for order in dashboard["recent_orders"]],
            [self.sample_order.pk],
        )


@override_settings(DEMO_MODE=False)
class ProductionAdminQueryCompatibilityTests(TestCase):
    def test_privacy_filter_is_a_noop_outside_demo_mode(self):
        visitor = User.objects.create_user(username="demo_customer_not_hidden")
        category = Category.objects.create(
            name="Production category",
            slug="production-privacy-category",
        )
        product = Product.objects.create(
            name="Production product",
            slug="production-privacy-product",
            category=category,
        )
        order = Order.objects.create(
            user=visitor,
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            unit_price=Decimal("0"),
            subtotal_price=Decimal("0"),
        )

        request = RequestFactory().get("/management/")
        request.user = User.objects.create_superuser(
            username="production-superuser",
            email="owner@example.invalid",
            password="OwnerPassword123!",
        )
        model_admin = admin.site._registry[Order]

        self.assertTrue(
            model_admin.get_queryset(request).filter(pk=order.pk).exists()
        )
