from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from catalog.models import (
    Category,
    InventoryMovement,
    Product,
    ProductVariant,
    Review,
    StorefrontSettings,
)
from orders.models import Order, Payment, ShippingMethod


User = get_user_model()


@override_settings(DEMO_MODE=True, DEBUG=False)
class DemoSeedCommandTests(TestCase):
    def test_seed_creates_catalog_reviews_shipping_and_full_order_lifecycle(self):
        manager = User.objects.create_superuser(
            username="demo-manager",
            email="manager@example.invalid",
            password="ManagerOnlyTestPassword123!",
        )

        call_command("seed_demo_data")

        self.assertTrue(User.objects.filter(pk=manager.pk).exists())
        self.assertEqual(Category.objects.count(), 3)
        self.assertEqual(Product.objects.count(), 8)
        self.assertEqual(ProductVariant.objects.count(), 16)
        self.assertEqual(Review.objects.count(), 8)
        self.assertEqual(ShippingMethod.objects.count(), 3)
        self.assertEqual(Order.objects.count(), 5)
        self.assertEqual(InventoryMovement.objects.count(), 3)

        order_statuses = {
            status: Order.objects.filter(status=status).count()
            for status, _label in Order.Status.choices
        }
        self.assertEqual(order_statuses[Order.Status.PAID], 1)
        self.assertEqual(order_statuses[Order.Status.PROCESSING], 1)
        self.assertEqual(order_statuses[Order.Status.COMPLETED], 1)
        self.assertEqual(order_statuses[Order.Status.PENDING], 1)
        self.assertEqual(order_statuses[Order.Status.CANCELLED], 1)

        self.assertEqual(
            Payment.objects.filter(status=Payment.Status.SUCCESS).count(),
            3,
        )
        self.assertEqual(
            Payment.objects.filter(status=Payment.Status.PENDING).count(),
            1,
        )
        self.assertEqual(
            Payment.objects.filter(status=Payment.Status.FAILED).count(),
            1,
        )

        storefront = StorefrontSettings.objects.get(pk=1)
        self.assertEqual(storefront.store_name, "فروشگاه نمونه Py_ShopOnline")

        self.assertEqual(Product.objects.get(slug="nova-x5-phone").stock, 23)
        self.assertEqual(Product.objects.get(slug="soundpods-pro").stock, 36)
        self.assertEqual(Product.objects.get(slug="fittime-smartwatch").stock, 18)

    def test_seed_is_repeatable_and_preserves_manager_account(self):
        manager = User.objects.create_superuser(
            username="demo-manager",
            email="manager@example.invalid",
            password="ManagerOnlyTestPassword123!",
        )

        call_command("seed_demo_data")
        call_command("seed_demo_data")

        self.assertTrue(User.objects.filter(pk=manager.pk).exists())
        self.assertEqual(Category.objects.count(), 3)
        self.assertEqual(Product.objects.count(), 8)
        self.assertEqual(Order.objects.count(), 5)
        self.assertEqual(Review.objects.count(), 8)
