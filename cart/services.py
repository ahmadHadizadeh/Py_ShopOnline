from django.core.exceptions import ValidationError
from django.db import transaction

from cart.models import Cart, CartItem

# cart/service


@transaction.atomic
def get_or_create_cart(request):
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(
            user=request.user,
            status=Cart.STATUS_ACTIVE,
        )
        return cart

    if not request.session.session_key:
        request.session.create()

    cart, _ = Cart.objects.get_or_create(
        session_key=request.session.session_key,
        user=None,
        status=Cart.STATUS_ACTIVE,
    )
    return cart


@transaction.atomic
def merge_guest_cart_to_user(request):
    if not request.user.is_authenticated:
        return get_or_create_cart(request)

    session_key = request.session.session_key
    if not session_key:
        return get_or_create_cart(request)

    user_cart, _ = Cart.objects.get_or_create(
        user=request.user,
        status=Cart.STATUS_ACTIVE,
    )

    guest_cart = (
        Cart.objects.select_for_update()
        .filter(
            session_key=session_key,
            user__isnull=True,
            status=Cart.STATUS_ACTIVE,
        )
        .first()
    )

    if not guest_cart or guest_cart.pk == user_cart.pk:
        return user_cart

    guest_items = guest_cart.items.select_for_update().select_related("product")

    for guest_item in guest_items:
        existing_item = (
            user_cart.items.select_for_update()
            .select_related("product")
            .filter(product=guest_item.product)
            .first()
        )

        if existing_item:
            merged_quantity = existing_item.quantity + guest_item.quantity
            stock_limit = guest_item.product.stock

            if merged_quantity > stock_limit:
                merged_quantity = stock_limit

            existing_item.quantity = max(1, merged_quantity)
            existing_item.status = CartItem.STATUS_ACTIVE
            existing_item.unit_price_snapshot = guest_item.product.price
            existing_item.full_clean()
            existing_item.save(
                update_fields=["quantity", "status", "unit_price_snapshot", "updated"]
            )
        else:
            guest_item.pk = None
            guest_item.cart = user_cart
            guest_item.status = (
                CartItem.STATUS_ACTIVE
                if guest_item.product.is_available and guest_item.product.stock > 0
                else CartItem.STATUS_SAVED
            )
            guest_item.quantity = min(
                guest_item.quantity, max(1, guest_item.product.stock)
            )
            guest_item.unit_price_snapshot = guest_item.product.price
            guest_item.full_clean()
            guest_item.save()

    guest_cart.delete()
    return user_cart


@transaction.atomic
def add_product_to_cart(cart, product, quantity=1):
    if quantity < 1:
        raise ValidationError("تعداد باید حداقل 1 باشد.")

    if not product.is_available:
        raise ValidationError("این محصول در حال حاضر قابل خرید نیست.")

    if quantity > product.stock:
        raise ValidationError("تعداد انتخاب‌شده بیشتر از موجودی محصول است.")

    item = (
        cart.items.select_for_update()
        .select_related("product")
        .filter(product=product)
        .first()
    )

    if item:
        new_quantity = item.quantity + quantity

        if new_quantity > product.stock:
            raise ValidationError("تعداد انتخاب‌شده بیشتر از موجودی محصول است.")

        item.quantity = new_quantity
        item.status = CartItem.STATUS_ACTIVE
        item.unit_price_snapshot = product.price
        item.full_clean()
        item.save(
            update_fields=["quantity", "status", "unit_price_snapshot", "updated"]
        )
        return item

    item = CartItem(
        cart=cart,
        product=product,
        quantity=quantity,
        unit_price_snapshot=product.price,
        status=CartItem.STATUS_ACTIVE,
    )
    item.full_clean()
    item.save()
    return item


@transaction.atomic
def update_cart_item_quantity(cart, product_id, quantity):
    if quantity < 1:
        raise ValidationError("تعداد باید حداقل 1 باشد.")

    item = (
        cart.items.select_for_update()
        .select_related("product")
        .filter(product_id=product_id, status=CartItem.STATUS_ACTIVE)
        .first()
    )

    if not item:
        raise ValidationError("آیتم موردنظر پیدا نشد.")

    if not item.product.is_available:
        raise ValidationError("این محصول در حال حاضر قابل خرید نیست.")

    if quantity > item.product.stock:
        raise ValidationError("تعداد انتخاب‌شده بیشتر از موجودی محصول است.")

    item.quantity = quantity
    item.unit_price_snapshot = item.product.price
    item.full_clean()
    item.save(update_fields=["quantity", "unit_price_snapshot", "updated"])
    return item


@transaction.atomic
def toggle_cart_item_status(cart, product_id, to_status):
    if to_status not in {CartItem.STATUS_ACTIVE, CartItem.STATUS_SAVED}:
        raise ValidationError("وضعیت نامعتبر است.")

    item = (
        cart.items.select_for_update()
        .select_related("product")
        .filter(product_id=product_id)
        .first()
    )

    if not item:
        raise ValidationError("آیتم موردنظر پیدا نشد.")

    if to_status == CartItem.STATUS_ACTIVE:
        if not item.product.is_available:
            raise ValidationError("این محصول در حال حاضر قابل خرید نیست.")

        if item.quantity > item.product.stock:
            raise ValidationError("تعداد این محصول بیشتر از موجودی فعلی است.")

    item.status = to_status
    item.unit_price_snapshot = item.product.price
    item.full_clean()
    item.save(update_fields=["status", "unit_price_snapshot", "updated"])
    return item


@transaction.atomic
def remove_product_from_cart(cart, product_id):
    item = cart.items.select_for_update().filter(product_id=product_id).first()

    if item:
        item.delete()
