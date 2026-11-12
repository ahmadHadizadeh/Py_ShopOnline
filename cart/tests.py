from decimal import Decimal

import pytest
from datetime import timedelta
from django.core.exceptions import ValidationError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import Sum
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from cart.models import Cart, CartItem
from cart.services import (
    add_product_to_cart,
    merge_guest_cart_to_user,
    toggle_cart_item_status_by_id,
    update_cart_item_quantity_by_id,
)
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

    def test_checkout_renders_persian_inline_address_error_without_creating_order(self):
        response = self.client.post(
            reverse("cart:checkout"),
            {
                "shipping_method_id": self.shipping_method.id,
                "recipient_name": "",
                "phone_number": "09123456789",
                "postal_code": "1234567890",
                "province": "تهران",
                "city": "تهران",
                "address_line": "آدرس تست",
                "is_default": False,
                "customer_note": "یادداشت اختیاری",
            },
        )

        content = response.content.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("نام گیرنده را وارد کنید.", content)
        self.assertIn('id="id_recipient_name_error"', content)
        self.assertIn("fa-circle-exclamation", content)
        self.assertEqual(content.count("نام گیرنده را وارد کنید."), 1)
        self.assertFalse(Order.objects.filter(user=self.user).exists())
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)

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

    def test_negative_snapshot_rejects_checkout_without_order_payment_or_address(self):
        original_from_db = CartItem.from_db

        def load_with_invalid_snapshot(model_cls, db, field_names, values):
            item = original_from_db(db, field_names, values)
            if item.pk == self.item.pk:
                item.unit_price_snapshot = Decimal("-0.01")
            return item

        with patch.object(
            CartItem,
            "from_db",
            classmethod(load_with_invalid_snapshot),
        ):
            response = self.client.post(
                reverse("cart:checkout"),
                self.checkout_payload(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertFalse(
            OrderAddressSnapshot.objects.filter(order__cart=self.cart).exists()
        )
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)

        # The invalid value existed only on the loaded test instance. The
        # database snapshot remains unchanged and was never rewritten.
        self.item.refresh_from_db()
        self.assertEqual(self.item.unit_price_snapshot, Decimal("1250.00"))

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

    def test_blank_shipping_method_id_returns_clear_validation_instead_of_500(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(
                shipping_method_id="",
                customer_note="این توضیح باید حفظ شود",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "برای ادامه خرید، لطفاً یکی از روش‌های ارسال را انتخاب کنید.")
        self.assertContains(response, "این توضیح باید حفظ شود")
        self.assertContains(response, "value=\"کاربر تست\"")
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)

    def test_non_numeric_shipping_method_id_returns_validation_instead_of_500(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(shipping_method_id="not-a-number"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "برای ادامه خرید، لطفاً یکی از روش‌های ارسال را انتخاب کنید.")
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

    def test_missing_address_and_shipping_report_both_errors_together(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(
                shipping_method_id="",
                recipient_name="",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "برای ادامه خرید، لطفاً یکی از روش‌های ارسال را انتخاب کنید.")
        self.assertContains(response, 'id="checkout-validation-summary"')
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

    def test_extremely_large_shipping_method_id_is_rejected_safely(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(shipping_method_id="9" * 100),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "برای ادامه خرید، لطفاً یکی از روش‌های ارسال را انتخاب کنید.")
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())

    def test_missing_required_address_field_displays_friendly_validation(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(recipient_name=""),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="checkout-validation-summary"')
        self.assertContains(response, "برای ادامه خرید، لطفاً موارد مشخص‌شده را تکمیل یا اصلاح کنید.")
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)

    def test_missing_city_shows_non_field_address_error(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(city=""),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="checkout-validation-summary"')
        self.assertContains(response, "لطفاً شهر مربوط به استان انتخاب شده را انتخاب کنید.")
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)

    def test_bound_checkout_preserves_edits_to_a_selected_saved_address(self):
        saved_address = Address.objects.create(
            user=self.user,
            recipient_name="گیرنده قبلی",
            phone_number="09123456789",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="آدرس قبلی",
            is_default=True,
        )

        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(
                address_id=saved_address.pk,
                recipient_name="نام ویرایش‌شده",
                postal_code="123",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-form-bound="true"')
        self.assertContains(response, f'value="{saved_address.pk}"')
        self.assertContains(response, 'value="نام ویرایش‌شده"')
        self.assertContains(response, "active-address")
        saved_address.refresh_from_db()
        self.assertEqual(saved_address.recipient_name, "گیرنده قبلی")
        self.assertEqual(saved_address.postal_code, "1234567890")

    def test_order_validation_error_is_displayed_and_saved_address_rolls_back(self):
        error_message = "موجودی یکی از محصولات تغییر کرده است؛ لطفاً سبد خرید را بررسی کنید."
        with patch(
            "cart.views.OrderService.create_order",
            side_effect=ValidationError(error_message),
        ):
            response = self.client.post(
                reverse("cart:checkout"),
                self.checkout_payload(customer_note="یادداشت حفظ‌شده"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="checkout-validation-summary"')
        self.assertContains(response, error_message)
        self.assertContains(response, "یادداشت حفظ‌شده")
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.assertFalse(Payment.objects.filter(order__cart=self.cart).exists())
        self.assertEqual(Address.objects.filter(user=self.user).count(), 0)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)

    def test_malformed_address_id_does_not_cause_server_error(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(address_id="not-a-number"),
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(cart=self.cart)
        self.assertEqual(order.address_snapshot.recipient_name, "کاربر تست")
        self.assertEqual(Address.objects.filter(user=self.user).count(), 1)

    def test_customer_note_is_optional_and_blank_note_does_not_block_checkout(self):
        response = self.client.post(
            reverse("cart:checkout"),
            self.checkout_payload(customer_note=""),
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(cart=self.cart)
        self.assertEqual(order.customer_note, "")

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
        self.assertIn("/accounts/login/", response.url)
        self.assertIn("next=", response.url)

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




class CartVariantPriceGuardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="variant-price-guard-user",
            password="testpass123",
        )
        self.category = Category.objects.create(
            name="Variant Price Guard Category",
            slug="variant-price-guard-category",
        )
        self.product = Product.objects.create(
            name="Variant Price Guard Product",
            slug="variant-price-guard-product",
            price=100,
            stock=10,
            is_active=True,
            category=self.category,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="قرمز",
            price_adjustment=Decimal("-20.00"),
        )
        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ACTIVE,
        )

    def test_add_uses_fresh_variant_value_instead_of_stale_view_instance(self):
        stale_variant = ProductVariant.objects.get(pk=self.variant.pk)
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            price_adjustment=Decimal("35.00")
        )

        item = add_product_to_cart(
            cart=self.cart,
            product=self.product,
            quantity=1,
            variant=stale_variant,
        )

        self.assertEqual(item.unit_price_snapshot, Decimal("135.00"))

    def test_add_rejects_negative_final_price_without_creating_cart_item(self):
        stale_variant = ProductVariant.objects.get(pk=self.variant.pk)
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            price_adjustment=Decimal("-100.01")
        )

        with self.assertRaises(ValidationError):
            add_product_to_cart(
                cart=self.cart,
                product=self.product,
                quantity=1,
                variant=stale_variant,
            )

        self.assertFalse(self.cart.items.exists())

    def test_zero_final_price_with_negative_adjustment_is_allowed(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            price_adjustment=Decimal("-100.00")
        )

        item = add_product_to_cart(
            cart=self.cart,
            product=self.product,
            quantity=1,
            variant=self.variant,
        )

        self.assertEqual(item.unit_price_snapshot, Decimal("0.00"))

    def test_quantity_update_rejects_new_negative_variant_price_atomically(self):
        item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            variant=self.variant,
            quantity=1,
            unit_price_snapshot=Decimal("80.00"),
            status=CartItem.STATUS_ACTIVE,
        )
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            price_adjustment=Decimal("-100.01")
        )

        with self.assertRaises(ValidationError):
            update_cart_item_quantity_by_id(self.cart, item.pk, 2)

        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.unit_price_snapshot, Decimal("80.00"))

    def test_reactivating_saved_item_rejects_negative_variant_price(self):
        item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            variant=self.variant,
            quantity=1,
            unit_price_snapshot=Decimal("80.00"),
            status=CartItem.STATUS_SAVED,
        )
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            price_adjustment=Decimal("-100.01")
        )

        with self.assertRaises(ValidationError):
            toggle_cart_item_status_by_id(
                self.cart, item.pk, CartItem.STATUS_ACTIVE
            )

        item.refresh_from_db()
        self.assertEqual(item.status, CartItem.STATUS_SAVED)
        self.assertEqual(item.unit_price_snapshot, Decimal("80.00"))

    def test_guest_merge_rejects_negative_variant_price_and_rolls_back(self):
        request = _merge_request(self.user)
        guest_cart = _guest_cart(request)
        guest_item = CartItem.objects.create(
            cart=guest_cart,
            product=self.product,
            variant=self.variant,
            quantity=1,
            unit_price_snapshot=Decimal("80.00"),
            status=CartItem.STATUS_SAVED,
        )
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            price_adjustment=Decimal("-100.01")
        )

        with self.assertRaises(ValidationError):
            merge_guest_cart_to_user(
                request,
                user=self.user,
                guest_session_key=request.session.session_key,
            )

        self.assertTrue(Cart.objects.filter(pk=guest_cart.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=guest_item.pk, cart=guest_cart).exists())
        self.assertTrue(
            Cart.objects.filter(
                pk=self.cart.pk,
                user=self.user,
                status=Cart.STATUS_ACTIVE,
            ).exists()
        )
        self.assertFalse(self.cart.items.exists())

    def test_cart_item_model_validation_rejects_negative_snapshot(self):
        item = CartItem(
            cart=self.cart,
            product=self.product,
            variant=self.variant,
            quantity=1,
            unit_price_snapshot=Decimal("-0.01"),
            status=CartItem.STATUS_SAVED,
        )

        with self.assertRaises(ValidationError) as raised:
            item.full_clean()

        self.assertIn("unit_price_snapshot", raised.exception.message_dict)

    def test_database_constraint_rejects_negative_snapshot_without_full_clean(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CartItem.objects.create(
                    cart=self.cart,
                    product=self.product,
                    variant=self.variant,
                    quantity=1,
                    unit_price_snapshot=Decimal("-0.01"),
                    status=CartItem.STATUS_SAVED,
                )


class SharedProductVariantStockTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="shared-variant-stock-user",
            password="testpass123",
        )
        self.category = Category.objects.create(
            name="Shared Variant Stock Category",
            slug="shared-variant-stock-category",
        )
        self.product = Product.objects.create(
            name="Shared Variant Stock Product",
            slug="shared-variant-stock-product",
            price=1000,
            stock=5,
            is_active=True,
            category=self.category,
        )
        self.red = ProductVariant.objects.create(
            product=self.product, name="رنگ", value="قرمز"
        )
        self.blue = ProductVariant.objects.create(
            product=self.product, name="رنگ", value="آبی"
        )
        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ACTIVE,
        )

    def add_item(self, variant, quantity, status=CartItem.STATUS_ACTIVE):
        return CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            variant=variant,
            quantity=quantity,
            unit_price_snapshot=self.product.price,
            status=status,
        )

    def test_add_rejects_total_across_variants_above_stock(self):
        self.add_item(self.red, 3)
        with self.assertRaisesMessage(ValidationError, "مجموع تعداد"):
            add_product_to_cart(
                self.cart, self.product, quantity=3, variant=self.blue
            )

        active_total = self.cart.items.filter(
            status=CartItem.STATUS_ACTIVE
        ).aggregate(total=Sum("quantity"))["total"]
        self.assertEqual(active_total, 3)
        self.assertFalse(self.cart.items.filter(variant=self.blue).exists())

    def test_quantity_update_counts_other_active_variants(self):
        red_item = self.add_item(self.red, 3)
        self.add_item(self.blue, 2)

        with self.assertRaises(ValidationError) as raised:
            update_cart_item_quantity_by_id(self.cart, red_item.pk, 4)

        self.assertIn("تمام واریانت", raised.exception.messages[0])
        red_item.refresh_from_db()
        self.assertEqual(red_item.quantity, 3)

    def test_saved_item_cannot_be_reactivated_above_shared_stock(self):
        self.add_item(self.red, 3)
        blue_item = self.add_item(self.blue, 3, status=CartItem.STATUS_SAVED)

        with self.assertRaises(ValidationError) as raised:
            toggle_cart_item_status_by_id(
                self.cart, blue_item.pk, CartItem.STATUS_ACTIVE
            )

        self.assertIn("تمام واریانت", raised.exception.messages[0])
        blue_item.refresh_from_db()
        self.assertEqual(blue_item.status, CartItem.STATUS_SAVED)

    def test_checkout_rejects_aggregate_quantity_across_variants(self):
        self.add_item(self.red, 3)
        self.add_item(self.blue, 3)
        address = Address.objects.create(
            user=self.user,
            recipient_name="کاربر تست",
            phone_number="09123456789",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="آدرس تست",
        )
        shipping = ShippingMethod.objects.create(
            name="Shared stock test shipping",
            cost=0,
        )

        with self.assertRaises(ValidationError) as raised:
            OrderService.create_order(
                user=self.user,
                cart=self.cart,
                shipping_address=address,
                shipping_method=shipping,
            )

        self.assertIn("تمام واریانت", raised.exception.messages[0])
        self.assertFalse(Order.objects.filter(cart=self.cart).exists())
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.STATUS_ACTIVE)
