import pytest
from datetime import timedelta
from django.core.exceptions import ValidationError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from cart.models import Cart, CartItem
from cart.services import merge_guest_cart_to_user
from catalog.models.product import Product

from accounts.models.address import Address
from catalog.models.category import Category
from catalog.models.variant import ProductVariant
from orders.models import Order, OrderAddressSnapshot, OrderItem, Payment
from orders.models.shipping import ShippingMethod
from orders.services import OrderService
from cart.views import CheckoutView


@pytest.mark.django_db
def test_cart_scenarios(client):
    # ۱. ساخت دسته‌بندی (الزامی برای دیتابیس)
    category = Category.objects.create(name="Test Category", slug="test-cat")

    # ۲. ساخت محصول با دسته‌بندی
    product = Product.objects.create(
        name="Test",
        slug="test",
        price=1000,
        stock=5,
        is_active=True,
        category=category,  # اضافه شد
    )

    # بقیه کد تست مثل قبل...
    client.post(reverse("cart:add", args=[product.id]), {"quantity": 2})
    cart = Cart.objects.first()
    assert cart.items.get(product=product).quantity == 2

    item = cart.items.get(product=product)
    client.post(reverse("cart:update_item", args=[item.id]), {"quantity": 4})
    assert cart.items.get(product=product).quantity == 4

    client.post(reverse("cart:save_for_later", args=[item.id]))
    assert cart.items.get(product=product).status == CartItem.STATUS_SAVED

    client.post(reverse("cart:move_to_cart", args=[item.id]))
    assert cart.items.get(product=product).status == CartItem.STATUS_ACTIVE


@pytest.mark.django_db
def test_cart_supports_multiple_variants_per_product(client):
    category = Category.objects.create(name="Variant Category", slug="variant-cat")
    product = Product.objects.create(
        name="Variant Product",
        slug="variant-product",
        price=1000,
        stock=10,
        is_active=True,
        category=category,
    )
    from catalog.models.variant import ProductVariant

    red = ProductVariant.objects.create(
        product=product, name="رنگ", value="قرمز", price_adjustment=100
    )
    blue = ProductVariant.objects.create(
        product=product, name="رنگ", value="آبی", price_adjustment=200
    )

    client.post(
        reverse("cart:add", args=[product.id]), {"quantity": 1, "variant_id": red.id}
    )
    client.post(
        reverse("cart:add", args=[product.id]), {"quantity": 2, "variant_id": blue.id}
    )

    cart = Cart.objects.first()
    assert cart.items.count() == 2
    assert cart.items.get(variant=red).unit_price_snapshot == 1100
    assert cart.items.get(variant=blue).unit_price_snapshot == 1200


@pytest.mark.django_db
def test_cart_requires_variant_when_product_has_variants(client):
    category = Category.objects.create(
        name="Required Variant Category", slug="required-variant-cat"
    )
    product = Product.objects.create(
        name="Required Variant Product",
        slug="required-variant-product",
        price=1000,
        stock=10,
        is_active=True,
        category=category,
    )
    from catalog.models.variant import ProductVariant

    ProductVariant.objects.create(product=product, name="حافظه", value="256GB")

    response = client.post(reverse("cart:add", args=[product.id]), {"quantity": 1})
    assert response.status_code == 302
    cart = Cart.objects.first()
    assert cart is None or cart.items.count() == 0


@pytest.mark.django_db
def test_order_receives_cart_variant_snapshot(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="variant-user", password="testpass"
    )
    client.force_login(user)
    category = Category.objects.create(
        name="Order Variant Category", slug="order-variant-cat"
    )
    product = Product.objects.create(
        name="Order Variant Product",
        slug="order-variant-product",
        price=1000,
        stock=10,
        is_active=True,
        category=category,
    )
    from catalog.models.variant import ProductVariant
    from accounts.models.address import Address
    from orders.models.shipping import ShippingMethod
    from orders.models.order_item import OrderItem
    from orders.services import OrderService

    variant = ProductVariant.objects.create(
        product=product, name="رنگ", value="قرمز", price_adjustment=250
    )
    cart = Cart.objects.create(user=user, status=Cart.STATUS_ACTIVE)
    CartItem.objects.create(
        cart=cart,
        product=product,
        variant=variant,
        quantity=2,
        unit_price_snapshot=1250,
        status=CartItem.STATUS_ACTIVE,
    )
    address = Address.objects.create(
        user=user,
        recipient_name="Test",
        phone_number="09123456789",
        postal_code="1234567890",
        province="تهران",
        city="تهران",
        address_line="Test Address",
    )
    shipping = ShippingMethod.objects.create(name="Test", cost=0, is_active=True)

    order = OrderService.create_order(
        user=user, cart=cart, shipping_address=address, shipping_method=shipping
    )
    item = OrderItem.objects.get(order=order)
    assert item.variant_id == variant.id
    assert item.variant_name == "رنگ: قرمز"
    assert item.unit_price == 1250
    cart.refresh_from_db()
    assert cart.status == Cart.STATUS_ORDERED
    assert cart.items.count() == 1


class CartVariantIntegrationTests(TestCase):

    def setUp(self):
        
        User = get_user_model()

        self.user = User.objects.create_user(
            username="variant-integration-user",
            password="testpass123",
        )

        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="Integration Category",
            slug="integration-category",
        )

        self.product = Product.objects.create(
            name="Integration Product",
            slug="integration-product",
            price=1000,
            stock=10,
            is_active=True,
            category=self.category,
        )

        self.red_variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="قرمز",
            price_adjustment=100,
        )

        self.blue_variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="آبی",
            price_adjustment=200,
        )

    def test_multiple_variants_can_exist_in_cart_for_same_product(self):
        self.client.post(
            reverse("cart:add", args=[self.product.id]),
            {
                "quantity": 1,
                "variant_id": self.red_variant.id,
            },
        )

        self.client.post(
            reverse("cart:add", args=[self.product.id]),
            {
                "quantity": 2,
                "variant_id": self.blue_variant.id,
            },
        )

        cart = Cart.objects.get(
            user=self.user,
            status=Cart.STATUS_ACTIVE,
        )

        self.assertEqual(
            cart.items.filter(product=self.product).count(),
            2,
        )

        red_item = cart.items.get(variant=self.red_variant)
        blue_item = cart.items.get(variant=self.blue_variant)

        self.assertEqual(red_item.quantity, 1)
        self.assertEqual(red_item.unit_price_snapshot, 1100)

        self.assertEqual(blue_item.quantity, 2)
        self.assertEqual(blue_item.unit_price_snapshot, 1200)

    def test_order_preserves_variant_snapshot(self):
        cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ACTIVE,
        )

        CartItem.objects.create(
            cart=cart,
            product=self.product,
            variant=self.red_variant,
            quantity=2,
            unit_price_snapshot=1100,
            status=CartItem.STATUS_ACTIVE,
        )

        address = Address.objects.create(
            user=self.user,
            recipient_name="Test User",
            phone_number="09123456789",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="Test Address",
        )

        shipping_method = ShippingMethod.objects.create(
            name="Test Shipping",
            cost=0,
            is_active=True,
        )

        order = OrderService.create_order(
            user=self.user,
            cart=cart,
            shipping_address=address,
            shipping_method=shipping_method,
        )

        order_item = OrderItem.objects.get(order=order)

        self.assertEqual(order_item.variant_id, self.red_variant.id)
        self.assertEqual(order_item.variant_name, "رنگ: قرمز")
        self.assertEqual(order_item.unit_price, 1100)

        cart.refresh_from_db()

        self.assertEqual(cart.status, Cart.STATUS_ORDERED)


class CheckoutVariantIntegrationTests(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()

        self.user = User.objects.create_user(
            username="checkout-variant-user",
            password="testpass123",
        )

        self.category = Category.objects.create(
            name="Checkout Category",
            slug="checkout-category",
        )

        self.product = Product.objects.create(
            name="Checkout Variant Product",
            slug="checkout-variant-product",
            price=1000,
            stock=10,
            is_active=True,
            category=self.category,
        )

        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="قرمز",
            price_adjustment=250,
        )

        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ACTIVE,
        )

        CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            variant=self.variant,
            quantity=2,
            unit_price_snapshot=1250,
            status=CartItem.STATUS_ACTIVE,
        )

        self.shipping_method = ShippingMethod.objects.create(
            name="Checkout Test Shipping",
            cost=100,
            is_active=True,
        )

        self.client.force_login(self.user)

    def test_checkout_creates_order_with_variant_snapshot(self):
        response = self.client.post(
            reverse("cart:checkout"),
            {
                "shipping_method_id": self.shipping_method.id,
                "recipient_name": "کاربر تست",
                "phone_number": "09123456789",
                "postal_code": "1234567890",
                "province": "تهران",
                "city": "تهران",
                "address_line": "آدرس تست",
                "is_default": False,
                "customer_note": "تست Checkout",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/orders/order/confirm/", response.url)

        self.cart.refresh_from_db()

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        order = self.cart.orders.get()

        self.assertEqual(
            order.subtotal_amount,
            2500,
        )

        self.assertEqual(
            order.shipping_amount,
            100,
        )

        self.assertEqual(
            order.final_amount,
            2600,
        )

        order_item = order.items.get()

        self.assertEqual(
            order_item.variant_id,
            self.variant.id,
        )

        self.assertEqual(
            order_item.variant_name,
            "رنگ: قرمز",
        )

        self.assertEqual(
            order_item.quantity,
            2,
        )

        self.assertEqual(
            order_item.unit_price,
            1250,
        )

        self.assertEqual(
            order_item.subtotal_price,
            2500,
        )

        self.assertEqual(
            order.payment.amount,
            2600,
        )


# ---------------------------------------------------------------------------
# Guest-cart merge contract tests
# ---------------------------------------------------------------------------


def _merge_request(user):
    request = RequestFactory().get("/")
    request.user = user
    request.session = SessionStore()
    request.session.create()
    return request


def _guest_cart(request):
    return Cart.objects.create(
        session_key=request.session.session_key,
        user=None,
        status=Cart.STATUS_ACTIVE,
    )


class GuestCartMergeContractTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="merge-user",
            password="testpass123",
        )
        self.category = Category.objects.create(
            name="Merge Category",
            slug="merge-category",
        )

    def product(self, *, name="Merge Product", stock=10, active=True):
        return Product.objects.create(
            name=name,
            slug=name.lower().replace(" ", "-"),
            price=1000,
            stock=stock,
            is_active=active,
            category=self.category,
        )

    def test_guest_only_item_is_moved_to_user_cart(self):
        product = self.product(stock=5)
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        guest_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=2,
            unit_price_snapshot=999,
            status=CartItem.STATUS_ACTIVE,
        )

        user_cart = merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        guest_item.refresh_from_db() if CartItem.objects.filter(pk=guest_item.pk).exists() else None
        self.assertFalse(Cart.objects.filter(pk=guest_cart.pk).exists())
        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.status, CartItem.STATUS_ACTIVE)
        self.assertEqual(item.unit_price_snapshot, 1000)
        self.assertEqual(request.session["cart_id"], user_cart.pk)

    def test_same_identity_quantities_are_summed_and_capped_by_product_stock(self):
        product = self.product(stock=5)
        user_cart = Cart.objects.create(user=self.user, status=Cart.STATUS_ACTIVE)
        CartItem.objects.create(
            cart=user_cart,
            product=product,
            quantity=3,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=4,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )

        merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 5)
        self.assertEqual(item.status, CartItem.STATUS_ACTIVE)

    def test_different_variants_share_product_stock_and_newer_item_gets_priority(self):
        product = self.product(stock=5)
        red = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="قرمز",
            price_adjustment=100,
        )
        blue = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="آبی",
            price_adjustment=200,
        )
        user_cart = Cart.objects.create(user=self.user, status=Cart.STATUS_ACTIVE)
        red_item = CartItem.objects.create(
            cart=user_cart,
            product=product,
            variant=red,
            quantity=3,
            unit_price_snapshot=1100,
            status=CartItem.STATUS_ACTIVE,
        )
        old = timezone.now() - timedelta(minutes=5)
        CartItem.objects.filter(pk=red_item.pk).update(updated=old)

        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        blue_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            variant=blue,
            quantity=4,
            unit_price_snapshot=1200,
            status=CartItem.STATUS_ACTIVE,
        )
        CartItem.objects.filter(pk=blue_item.pk).update(updated=timezone.now())

        merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        self.assertEqual(user_cart.items.get(variant=blue).quantity, 4)
        self.assertEqual(user_cart.items.get(variant=red).quantity, 1)
        self.assertEqual(
            sum(
                user_cart.items.filter(
                    product=product, status=CartItem.STATUS_ACTIVE
                ).values_list("quantity", flat=True)
            ),
            5,
        )

    def test_equal_updated_timestamp_gives_guest_priority(self):
        product = self.product(stock=5)
        red = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="قرمز",
            price_adjustment=100,
        )
        blue = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="آبی",
            price_adjustment=200,
        )
        user_cart = Cart.objects.create(user=self.user, status=Cart.STATUS_ACTIVE)
        red_item = CartItem.objects.create(
            cart=user_cart,
            product=product,
            variant=red,
            quantity=4,
            unit_price_snapshot=1100,
            status=CartItem.STATUS_ACTIVE,
        )
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        blue_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            variant=blue,
            quantity=4,
            unit_price_snapshot=1200,
            status=CartItem.STATUS_ACTIVE,
        )
        same_updated = timezone.now() - timedelta(minutes=1)
        CartItem.objects.filter(pk=red_item.pk).update(updated=same_updated)
        CartItem.objects.filter(pk=blue_item.pk).update(updated=same_updated)

        merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        self.assertEqual(user_cart.items.get(variant=blue).quantity, 4)
        self.assertEqual(user_cart.items.get(variant=red).quantity, 1)

    def test_latest_status_wins_for_duplicate_identity(self):
        product = self.product(stock=10)
        user_cart = Cart.objects.create(user=self.user, status=Cart.STATUS_ACTIVE)
        user_item = CartItem.objects.create(
            cart=user_cart,
            product=product,
            quantity=2,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        guest_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=3,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_SAVED,
        )
        newer = timezone.now()
        CartItem.objects.filter(pk=user_item.pk).update(updated=newer - timedelta(minutes=1))
        CartItem.objects.filter(pk=guest_item.pk).update(updated=newer)

        merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 5)
        self.assertEqual(item.status, CartItem.STATUS_SAVED)

    def test_active_newer_guest_status_wins_for_duplicate_identity(self):
        product = self.product(stock=10)
        user_cart = Cart.objects.create(user=self.user, status=Cart.STATUS_ACTIVE)
        user_item = CartItem.objects.create(
            cart=user_cart,
            product=product,
            quantity=2,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_SAVED,
        )
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        guest_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=3,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )
        newer = timezone.now()
        CartItem.objects.filter(pk=user_item.pk).update(updated=newer - timedelta(minutes=1))
        CartItem.objects.filter(pk=guest_item.pk).update(updated=newer)

        merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 5)
        self.assertEqual(item.status, CartItem.STATUS_ACTIVE)

    def test_saved_items_sum_without_stock_cap(self):
        product = self.product(stock=1)
        user_cart = Cart.objects.create(user=self.user, status=Cart.STATUS_ACTIVE)
        CartItem.objects.create(
            cart=user_cart,
            product=product,
            quantity=3,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_SAVED,
        )
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=4,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_SAVED,
        )

        merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 7)
        self.assertEqual(item.status, CartItem.STATUS_SAVED)

    def test_active_item_becomes_saved_when_product_unavailable(self):
        product = self.product(stock=5, active=False)
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=3,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )

        user_cart = merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.status, CartItem.STATUS_SAVED)

    def test_merge_is_idempotent_after_guest_cart_is_consumed(self):
        product = self.product(stock=5)
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=2,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )

        first = merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )
        second = merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.items.get(product=product).quantity, 2)
        self.assertEqual(
            Cart.objects.filter(user=self.user, status=Cart.STATUS_ACTIVE).count(),
            1,
        )

    def test_active_item_becomes_saved_when_stock_is_zero(self):
        product = self.product(stock=0)
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        CartItem.objects.create(
            cart=guest_cart,
            product=product,
            quantity=2,
            unit_price_snapshot=1000,
            status=CartItem.STATUS_ACTIVE,
        )

        user_cart = merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        item = user_cart.items.get(product=product)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.status, CartItem.STATUS_SAVED)

    def test_empty_guest_cart_is_consumed_without_creating_items(self):
        product = self.product(stock=5)
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)

        user_cart = merge_guest_cart_to_user(
            request,
            user=self.user,
            guest_session_key=request.session.session_key,
        )

        self.assertFalse(Cart.objects.filter(pk=guest_cart.pk).exists())
        self.assertFalse(user_cart.items.filter(product=product).exists())

    def test_merge_rolls_back_when_persisting_an_item_fails(self):
        product = self.product(stock=10)
        red = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="قرمز",
            price_adjustment=100,
        )
        blue = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="آبی",
            price_adjustment=200,
        )
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        red_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            variant=red,
            quantity=1,
            unit_price_snapshot=1100,
            status=CartItem.STATUS_ACTIVE,
        )
        blue_item = CartItem.objects.create(
            cart=guest_cart,
            product=product,
            variant=blue,
            quantity=1,
            unit_price_snapshot=1200,
            status=CartItem.STATUS_ACTIVE,
        )

        import cart.services as services
        real_unit_price = services._unit_price
        calls = {"count": 0}

        def failing_unit_price(product_obj, variant=None):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced merge failure")
            return real_unit_price(product_obj, variant)

        with patch("cart.services._unit_price", side_effect=failing_unit_price):
            with self.assertRaises(RuntimeError):
                merge_guest_cart_to_user(
                    request,
                    user=self.user,
                    guest_session_key=request.session.session_key,
                )

        self.assertTrue(Cart.objects.filter(pk=guest_cart.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=red_item.pk, cart=guest_cart).exists())
        self.assertTrue(CartItem.objects.filter(pk=blue_item.pk, cart=guest_cart).exists())
        user_cart = Cart.objects.filter(
            user=self.user, status=Cart.STATUS_ACTIVE
        ).first()
        self.assertIsNone(user_cart)


# ---------------------------------------------------------------------------
# Checkout / Order integrity contract tests
# ---------------------------------------------------------------------------


class CheckoutOrderIntegrityContractTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="checkout-integrity-user",
            password="testpass123",
        )
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="Checkout Integrity Category",
            slug="checkout-integrity-category",
        )
        self.product = Product.objects.create(
            name="Checkout Integrity Product",
            slug="checkout-integrity-product",
            price=1000,
            stock=10,
            is_active=True,
            category=self.category,
        )
        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ACTIVE,
        )
        self.item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            quantity=2,
            unit_price_snapshot=1250,
            status=CartItem.STATUS_ACTIVE,
        )
        self.shipping_method = ShippingMethod.objects.create(
            name="Checkout Integrity Shipping",
            cost=100,
            is_active=True,
        )

    def checkout_payload(self, **overrides):
        payload = {
            "shipping_method_id": self.shipping_method.id,
            "recipient_name": "کاربر تست",
            "phone_number": "09123456789",
            "postal_code": "1234567890",
            "province": "تهران",
            "city": "تهران",
            "address_line": "آدرس تست",
            "is_default": False,
            "customer_note": "تست قرارداد Checkout",
        }
        payload.update(overrides)
        return payload

    def test_empty_cart_cannot_create_order(self):
        self.item.delete()

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Cart.objects.get(pk=self.cart.pk).status,
            Cart.STATUS_ACTIVE,
        )
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())

    def test_ordered_cart_cannot_be_checked_out_again(self):
        self.cart.status = Cart.STATUS_ORDERED
        self.cart.save(update_fields=["status"])

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertEqual(
            Cart.objects.get(pk=self.cart.pk).status,
            Cart.STATUS_ORDERED,
        )

    def test_invalid_shipping_method_does_not_create_order(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(shipping_method_id=999999),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Cart.objects.get(pk=self.cart.pk).status,
            Cart.STATUS_ACTIVE,
        )

    def test_inactive_shipping_method_does_not_create_order(self):
        self.shipping_method.is_active = False
        self.shipping_method.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

    def test_invalid_address_does_not_create_order_or_persist_address(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(postal_code="123"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

    def test_foreign_address_id_can_never_be_used_for_checkout(self):
        other_user = get_user_model().objects.create_user(
            username="checkout-other-user",
            password="testpass123",
        )
        foreign_address = Address.objects.create(
            user=other_user,
            recipient_name="کاربر دیگر",
            phone_number="09120000000",
            postal_code="1111111111",
            province="تهران",
            city="تهران",
            address_line="آدرس کاربر دیگر",
        )

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(
                address_id=foreign_address.id,
                recipient_name="گیرنده من",
                address_line="آدرس من",
            ),
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(cart=self.cart)
        snapshot = order.address_snapshot

        self.assertEqual(snapshot.recipient_name, "گیرنده من")
        self.assertEqual(snapshot.address_line, "آدرس من")

        foreign_address.refresh_from_db()
        self.assertEqual(foreign_address.recipient_name, "کاربر دیگر")
        self.assertEqual(foreign_address.address_line, "آدرس کاربر دیگر")

    def test_catalog_price_change_does_not_override_cart_price_snapshot(self):
        self.product.price = 9999
        self.product.save(update_fields=["price"])

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(cart=self.cart)
        order_item = order.items.get()

        self.assertEqual(order_item.unit_price, self.item.unit_price_snapshot)
        self.assertEqual(order_item.subtotal_price, self.item.unit_price_snapshot * self.item.quantity)
        self.assertEqual(order.subtotal_amount, self.item.unit_price_snapshot * self.item.quantity)

    def test_address_snapshot_is_historical(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)

        order = Order.objects.get(cart=self.cart)
        snapshot = order.address_snapshot
        address = Address.objects.get(user=self.user)

        address.recipient_name = "نام جدید"
        address.address_line = "آدرس جدید"
        address.save(update_fields=["recipient_name", "address_line"])

        snapshot.refresh_from_db()

        self.assertEqual(snapshot.recipient_name, "کاربر تست")
        self.assertEqual(snapshot.address_line, "آدرس تست")

    def test_checkout_does_not_reduce_stock(self):
        initial_stock = self.product.stock

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)

        self.product.refresh_from_db()
        order = Order.objects.get(cart=self.cart)

        self.assertEqual(self.product.stock, initial_stock)
        self.assertFalse(order.stock_reduced)

    def test_saved_only_cart_cannot_create_order(self):
        self.item.status = CartItem.STATUS_SAVED
        self.item.save(update_fields=["status"])

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("cart", response.url)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Cart.objects.get(pk=self.cart.pk).status,
            Cart.STATUS_ACTIVE,
        )

    def test_checkout_get_rejects_saved_only_cart(self):
        self.item.status = CartItem.STATUS_SAVED
        self.item.save(update_fields=["status"])

        response = self.client.get(reverse("cart:checkout"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("cart", response.url)

    def test_checkout_mixed_cart_creates_order_from_active_items_only(self):
        saved_product = Product.objects.create(
            name="Saved Only Product",
            slug="saved-only-product",
            price=700,
            stock=10,
            is_active=True,
            category=self.category,
        )
        CartItem.objects.create(
            cart=self.cart,
            product=saved_product,
            quantity=3,
            unit_price_snapshot=700,
            status=CartItem.STATUS_SAVED,
        )

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)

        order = Order.objects.get(cart=self.cart)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().product_id, self.product.id)
        self.assertFalse(
            order.items.filter(product_id=saved_product.id).exists()
        )

    def test_guest_post_cannot_enter_checkout_transaction(self):
        self.client.logout()

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts:login", response.url)

    def test_order_service_failure_rolls_back_address_order_payment_and_cart(self):
        with patch(
            "cart.views.OrderService.create_order",
            side_effect=ValidationError("forced checkout failure"),
        ):
            response = self.client.post(
                reverse("cart:checkout"),
                self.checkout_payload(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)

    def test_checkout_is_idempotent_after_first_success(self):
        first_response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )
        self.assertEqual(first_response.status_code, 302)

        order_count = Order.objects.filter(cart=self.cart).count()

        second_response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Order.objects.filter(cart=self.cart).count(), order_count)
        self.assertEqual(order_count, 1)

    def test_shipping_calculation_failure_never_falls_back_to_zero_cost(self):
        request = RequestFactory().get(reverse("cart:checkout"))
        request.user = self.user

        with patch.object(
            ShippingMethod,
            "calculate_shipping_cost",
            side_effect=RuntimeError("forced shipping calculation failure"),
        ):
            data = CheckoutView().get_shipping_methods_data(request, self.cart)

        self.assertEqual(data, [])

    def test_payment_amount_matches_final_order_amount(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(),
        )

        self.assertEqual(response.status_code, 302)

        order = Order.objects.get(cart=self.cart)
        self.assertEqual(order.payment.amount, order.final_amount)
