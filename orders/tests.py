import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.db import (
    transaction,
    models,
)  # اضافه کردن models برای دسترسی به models.DEFERRED
from django.utils import timezone  # برای استفاده در تست status changes

# Import models based on common Django project structure
# Adjust these paths if your app structure is different
from catalog.models import Product, Category
from cart.models import Cart, CartItem
from accounts.models import Address
from django.core.files.uploadedfile import SimpleUploadedFile

from orders.models import (
    Order,
    OrderItem,
    OrderAddressSnapshot,
    Payment,
)

User = get_user_model()


class CheckoutFlowTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # --- Setup User ---
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="password123"
        )
        self.client.force_login(self.user)

        # --- Setup Category and Product ---
        self.category = self._create_category(name="Electronics", slug="electronics")
        self.product = self._create_product(
            name="Test Product",
            slug="test-product",
            price=250000,
            stock=10,
            category=self.category,
        )
        self.product_expensive = self._create_product(
            name="Expensive Product",
            slug="expensive-product",
            price=5000000,
            stock=1,
            category=self.category,
        )
        self.product_out_of_stock = self._create_product(
            name="Out of Stock Product",
            slug="out-of-stock-product",
            price=100000,
            stock=0,
            category=self.category,
        )

        # --- Setup Address ---
        self.address = self._create_address(
            user=self.user,
            recipient_name="احمد هادی‌زاده",
            phone_number="09123456789",
            postal_code="1234567890",
            province="Mazandaran",
            city="Amol",
            address_line="Amol, Test Street, No 10",
            is_default=True,
        )

        # --- Setup Active Cart ---
        self.cart = self._create_cart(user=self.user)
        self.cart_item_1 = self._create_cart_item(
            cart=self.cart, product=self.product, quantity=2
        )
        self.cart_item_2 = self._create_cart_item(
            cart=self.cart, product=self.product_expensive, quantity=1
        )

    # --- Helper methods for instance creation ---
    def _generic_value(self, field_name, value=None, instance=None):
        # Placeholder for more sophisticated field value generation if needed
        return value

    def _create_instance(self, model, **kwargs):
        opts = model._meta

        # استخراج نام فیلدهای واقعی دیتابیس
        concrete_field_names = {
            f.name for f in opts.get_fields() if f.concrete and not f.many_to_many
        }

        # فیلتر کردن kwargs برای حذف Propertyها یا آرگومان‌های نامعتبر
        valid_fields = {k: v for k, v in kwargs.items() if k in concrete_field_names}

        # مقداردهی به فیلدهای الزامی که در kwargs نیستند
        for field in opts.get_fields():
            if (
                field.name in valid_fields
                or not field.concrete
                or field.many_to_many
                or field.auto_created
            ):
                continue

            if field.default != models.fields.NOT_PROVIDED:
                valid_fields[field.name] = field.default
            elif not field.null and field.editable and field.name != "id":
                if field.name == "slug" and "name" in kwargs:
                    valid_fields[field.name] = kwargs["name"].lower().replace(" ", "-")
                elif field.name == "order_number" and hasattr(
                    model, "generate_order_number"
                ):
                    valid_fields[field.name] = model.generate_order_number()

        return model.objects.create(**valid_fields)

    def _create_category(self, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = None
        return Category.objects.create(**kwargs)

    # ... در کلاس تست
    def _create_product(self, **kwargs):
        if "image" not in kwargs:
            kwargs["image"] = SimpleUploadedFile(
                name="test_image.jpg", content=b"", content_type="image/jpeg"
            )
        return self._create_instance(Product, **kwargs)

    def _create_address(self, **kwargs):
        if "user" not in kwargs:
            kwargs["user"] = self.user  # Default to logged-in user if not specified
        return self._create_instance(Address, **kwargs)

    def _create_cart(self, **kwargs):
        if "user" not in kwargs:
            kwargs["user"] = self.user
        return self._create_instance(Cart, **kwargs)

    def _create_cart_item(self, cart, product, **kwargs):
        kwargs.setdefault("cart", cart)
        kwargs.setdefault("product", product)
        kwargs.setdefault("quantity", 1)

        # استفاده از فیلد واقعی دیتابیس به جای پراپرتی‌های مجازی
        kwargs.setdefault("unit_price_snapshot", product.price)

        # حذف subtotal_price از kwargs چون در مدل فیلد نیست
        kwargs.pop("subtotal_price", None)
        kwargs.pop("unit_price", None)
        return self._create_instance(CartItem, **kwargs)

    def test_01_add_to_cart_and_view_detail(self):
        """Test adding items to cart and viewing the cart detail page."""
        # Items are already added in setUp, just verify cart detail
        url = reverse("cart:detail")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cart/detail.html")
        self.assertEqual(response.context["cart"].items.count(), 2)
        # Check total quantities and prices are calculated correctly
        self.assertContains(response, "Test Product")
        self.assertContains(response, "Expensive Product")
        # Verify total amount is correctly displayed (2*250000 + 1*5000000 = 5500000)
        self.assertContains(response, "5,500,000")  # Assuming currency formatting

    def test_02_checkout_process_success(self):
        """Test the complete checkout flow leading to successful payment."""
        checkout_url = reverse("cart:checkout")

        # --- Step 1: POST to checkout to create order and redirect to payment ---
        response = self.client.post(
            checkout_url,
            {
                "shipping_address": self.address.pk,  # Assuming address is selected via PK
                "customer_note": "Please deliver quickly.",
            },
        )

        # We need to get the order number from the response or by fetching the last pending order
        try:
            order = Order.objects.filter(
                user=self.user, status=Order.Status.PENDING
            ).latest("created")
            self.assertEqual(order.customer_note, "Please deliver quickly.")
            self.assertEqual(
                order.address_snapshot.recipient_name, self.address.recipient_name
            )
            self.assertIsNotNone(order.payment)
            self.assertEqual(order.payment.status, Payment.Status.PENDING)

            initiate_payment_url = reverse(
                "orders:initiate_payment", kwargs={"order_number": order.order_number}
            )
            response_initiate = self.client.get(initiate_payment_url)

            self.assertRedirects(
                response_initiate,
                f"/orders/mock-payment-gateway/?trxid={order.payment.transaction_code}&order={order.order_number}&amount={order.final_amount}",
            )

        except Order.DoesNotExist:
            self.fail("Order was not created during checkout.")

        payment_record = Payment.objects.get(order=order)
        callback_url = reverse("orders:payment_callback")
        mock_callback_response = self.client.get(
            f"{callback_url}?trxid={payment_record.transaction_code}&status=success"
        )

        # --- Step 3: Verify Payment Success Redirect ---
        self.assertRedirects(
            mock_callback_response,
            reverse(
                "orders:payment_success", kwargs={"order_number": order.order_number}
            ),
        )

        # --- Step 4: Verify Order and Payment Status after callback ---
        updated_order = Order.objects.get(pk=order.pk)
        updated_payment = Payment.objects.get(pk=payment_record.pk)

        self.assertEqual(updated_order.status, Order.Status.PAID)
        self.assertIsNotNone(updated_order.paid_at)
        self.assertTrue(updated_order.stock_reduced)  # Stock should be reduced

        self.assertEqual(updated_payment.status, Payment.Status.SUCCESS)
        self.assertIsNotNone(updated_payment.reference_code)
        self.assertIn("status=success", updated_payment.gateway_response)

        # Verify stock reduction
        product_after_payment = Product.objects.get(pk=self.product.pk)
        product_expensive_after_payment = Product.objects.get(
            pk=self.product_expensive.pk
        )

        self.assertEqual(product_after_payment.stock, 10 - 2)  # Original 10 - 2 items
        self.assertEqual(
            product_expensive_after_payment.stock, 1 - 1
        )  # Original 1 - 1 item

        # Verify cart is empty
        self.assertEqual(Cart.objects.get(pk=self.cart.pk).items.count(), 0)

    def test_03_checkout_process_payment_failed(self):
        """Test checkout flow when payment fails."""
        checkout_url = reverse("cart:checkout")
        response = self.client.post(
            checkout_url,
            {
                "shipping_address": self.address.pk,
                "customer_note": "Trying failed payment.",
            },
        )

        try:
            order = Order.objects.filter(
                user=self.user, status=Order.Status.PENDING
            ).latest("created")
            payment_record = Payment.objects.get(order=order)

            initiate_payment_url = reverse(
                "orders:initiate_payment", kwargs={"order_number": order.order_number}
            )
            response_initiate = self.client.get(initiate_payment_url)
            self.assertRedirects(
                response_initiate,
                f"/orders/mock-payment-gateway/?trxid={payment_record.transaction_code}&order={order.order_number}&amount={order.final_amount}",
            )

        except Order.DoesNotExist:
            self.fail("Order was not created during checkout.")

        # --- Simulate Payment Gateway Callback for Failure ---
        callback_url = reverse("orders:payment_callback")
        mock_callback_response = self.client.get(
            f"{callback_url}?trxid={payment_record.transaction_code}&status=failed"
        )

        # --- Verify Payment Failed Redirect ---
        self.assertRedirects(
            mock_callback_response,
            reverse(
                "orders:payment_failed", kwargs={"order_number": order.order_number}
            ),
        )

        # --- Verify Order and Payment Status after failed callback ---
        updated_order = Order.objects.get(pk=order.pk)
        updated_payment = Payment.objects.get(pk=payment_record.pk)

        self.assertEqual(
            updated_order.status, Order.Status.PENDING
        )  # Order status should remain PENDING
        self.assertIsNone(updated_order.paid_at)
        self.assertFalse(updated_order.stock_reduced)  # Stock should NOT be reduced

        self.assertEqual(updated_payment.status, Payment.Status.FAILED)
        self.assertIn("status=failed", updated_payment.gateway_response)

        # Verify stock remains unchanged
        product_after_fail = Product.objects.get(pk=self.product.pk)
        self.assertEqual(
            product_after_fail.stock, 10
        )  # Stock should be the same as before

    def test_04_payment_callback_idempotency(self):
        """Test that processing the same callback twice does not change state."""
        checkout_url = reverse("cart:checkout")
        response = self.client.post(
            checkout_url,
            {
                "shipping_address": self.address.pk,
            },
        )

        try:
            order = Order.objects.filter(
                user=self.user, status=Order.Status.PENDING
            ).latest("created")
            payment_record = Payment.objects.get(order=order)
        except Order.DoesNotExist:
            self.fail("Order was not created during checkout.")

        callback_url = reverse("orders:payment_callback")

        # --- First callback (success) ---
        response1 = self.client.get(
            f"{callback_url}?trxid={payment_record.transaction_code}&status=success"
        )
        self.assertRedirects(
            response1,
            reverse(
                "orders:payment_success", kwargs={"order_number": order.order_number}
            ),
        )
        payment_after_first_callback = Payment.objects.get(pk=payment_record.pk)
        order_after_first_callback = Order.objects.get(pk=order.pk)

        self.assertEqual(payment_after_first_callback.status, Payment.Status.SUCCESS)
        self.assertEqual(order_after_first_callback.status, Order.Status.PAID)
        self.assertTrue(order_after_first_callback.stock_reduced)

        # --- Second callback with same trxid (should have no effect) ---
        response2 = self.client.get(
            f"{callback_url}?trxid={payment_record.transaction_code}&status=success"  # Status doesn't matter here for idempotency test
        )
        self.assertRedirects(
            response2,
            reverse(
                "orders:payment_success", kwargs={"order_number": order.order_number}
            ),
        )

        payment_after_second_callback = Payment.objects.get(pk=payment_record.pk)
        order_after_second_callback = Order.objects.get(pk=order.pk)

        # Verify state remains unchanged
        self.assertEqual(payment_after_second_callback.status, Payment.Status.SUCCESS)
        self.assertEqual(order_after_second_callback.status, Order.Status.PAID)
        self.assertTrue(order_after_second_callback.stock_reduced)
        self.assertEqual(
            payment_after_second_callback.pk, payment_after_first_callback.pk
        )  # Ensure it's the same payment record

    def test_05_checkout_with_out_of_stock_product(self):
        """Test that checkout fails if an item in the cart is out of stock."""
        # Add an out-of-stock product to the cart
        out_of_stock_cart_item = self._create_cart_item(
            cart=self.cart, product=self.product_out_of_stock, quantity=1
        )

        checkout_url = reverse("cart:checkout")
        response = self.client.post(
            checkout_url,
            {
                "shipping_address": self.address.pk,
            },
        )

        # Expecting a redirect back to the cart detail page with an error message
        # The exact redirect URL and error message handling might depend on your checkout view's implementation
        self.assertRedirects(response, reverse("cart:detail"))

        # Check for error message in session (assuming checkout view sets it)
        # Note: Accessing session directly might require a different approach depending on test setup
        # For simplicity, we check if an order was NOT created
        self.assertFalse(
            Order.objects.filter(user=self.user, status=Order.Status.PENDING).exists()
        )

        # A more robust test would check for a specific message flashed to the user
        # This might require a different way to check messages in tests

    def test_06_payment_callback_missing_trxid(self):
        """Test callback with missing transaction ID."""
        callback_url = reverse("orders:payment_callback")
        response = self.client.get(callback_url)  # Missing trxid and status

        self.assertEqual(response.status_code, 400)  # Expecting BadRequest
        self.assertContains(response, "خطا: شناسه تراکنش درگاه یافت نشد.")

    def test_07_order_status_changes_correctly(self):
        """Verify order status transitions."""
        # Create a pending order
        with transaction.atomic():
            order = Order.objects.create(
                user=self.user,
                order_number=Order.generate_order_number(),
                status=Order.Status.PENDING,
                subtotal_amount=10000,
                final_amount=10000,
            )
            payment = Payment.objects.create(
                order=order,
                user=self.user,
                amount=10000,
                status=Payment.Status.PENDING,
                transaction_code="TEST_TRX_123",
            )

        # Simulate successful payment callback
        callback_url = reverse("orders:payment_callback")
        self.client.get(
            f"{callback_url}?trxid={payment.transaction_code}&status=success"
        )

        updated_order = Order.objects.get(pk=order.pk)
        self.assertEqual(updated_order.status, Order.Status.PAID)
        self.assertIsNotNone(updated_order.paid_at)

        # Simulate cancellation (e.g., before payment)
        order.status = Order.Status.PENDING  # Reset status
        order.cancelled_at = None
        order.save()
        order.status = Order.Status.CANCELLED
        order.cancelled_at = timezone.now()  # Simulate cancellation time
        order.save(update_fields=["status", "cancelled_at"])

        updated_order_cancelled = Order.objects.get(pk=order.pk)
        self.assertEqual(updated_order_cancelled.status, Order.Status.CANCELLED)
        self.assertIsNotNone(updated_order_cancelled.cancelled_at)
