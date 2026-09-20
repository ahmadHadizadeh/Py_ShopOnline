from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from cart.models import Cart, CartItem
from catalog.models.product import Product


def _validate_variant(product, variant):
    if variant is None:
        if product.variants.filter().exists():
            raise ValidationError("برای این محصول باید یک واریانت انتخاب کنید.")
        return
    if variant.product_id != product.id:
        raise ValidationError("واریانت انتخاب‌شده متعلق به این محصول نیست.")


def _unit_price(product, variant=None):
    adjustment = variant.price_adjustment if variant is not None else Decimal("0")
    return product.price + adjustment


@transaction.atomic
def get_or_create_cart(request):
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user, status=Cart.STATUS_ACTIVE)
        return cart

    if not request.session.session_key:
        request.session.create()

    cart, _ = Cart.objects.get_or_create(
        session_key=request.session.session_key,
        user=None,
        status=Cart.STATUS_ACTIVE,
    )
    request.session["cart_id"] = cart.pk
    return cart


@transaction.atomic
def merge_guest_cart_to_user(request, *, user=None, guest_session_key=None):
    """Merge a guest/session cart into a user's active cart.

    The optional ``user`` and ``guest_session_key`` arguments let the OTP flow
    capture the guest session key before Django rotates the session during
    ``login()``. Existing callers that invoke this function after login remain
    supported through the request's authenticated user/session key.
    """
    target_user = user if user is not None else request.user

    if target_user is None or not getattr(target_user, "is_authenticated", False):
        return get_or_create_cart(request)

    session_key = guest_session_key or request.session.session_key

    # Serialize merges for the same user and make active-cart creation
    # deterministic inside this transaction.
    user_model = target_user.__class__
    target_user = user_model.objects.select_for_update().get(pk=target_user.pk)

    user_cart = (
        Cart.objects.select_for_update()
        .filter(user=target_user, status=Cart.STATUS_ACTIVE)
        .first()
    )
    if user_cart is None:
        user_cart = Cart.objects.create(
            user=target_user,
            status=Cart.STATUS_ACTIVE,
        )

    if not session_key:
        request.session["cart_id"] = user_cart.pk
        request.session.modified = True
        return user_cart

    guest_cart = (
        Cart.objects.select_for_update()
        .filter(
            session_key=session_key,
            user__isnull=True,
            status=Cart.STATUS_ACTIVE,
        )
        .first()
    )

    if guest_cart is None or guest_cart.pk == user_cart.pk:
        request.session["cart_id"] = user_cart.pk
        request.session.modified = True
        return user_cart

    guest_items = list(
        guest_cart.items.select_for_update()
        .select_related("product", "variant")
        .order_by("id")
    )
    if not guest_items:
        guest_cart.delete()
        request.session["cart_id"] = user_cart.pk
        request.session.modified = True
        return user_cart

    product_ids = {item.product_id for item in guest_items}
    user_items = list(
        user_cart.items.select_for_update()
        .select_related("product", "variant")
        .filter(product_id__in=product_ids)
        .order_by("id")
    )

    # Product is the stock authority in the current architecture. Lock every
    # affected Product in a deterministic order before calculating allocations.
    products = {
        product.id: product
        for product in Product.objects.select_for_update()
        .filter(id__in=product_ids)
        .order_by("id")
    }

    guest_by_identity = {
        (item.product_id, item.variant_id): item for item in guest_items
    }
    user_by_identity = {
        (item.product_id, item.variant_id): item for item in user_items
    }

    affected_identities = sorted(
        set(guest_by_identity) | set(user_by_identity),
        key=lambda value: (value[0], value[1] or 0),
    )

    for product_id in sorted(product_ids):
        product = products[product_id]
        identities = [identity for identity in affected_identities if identity[0] == product_id]

        candidates = []
        for identity in identities:
            guest_item = guest_by_identity.get(identity)
            user_item = user_by_identity.get(identity)

            if guest_item is not None and user_item is not None:
                if guest_item.updated >= user_item.updated:
                    latest_item = guest_item
                    latest_is_guest = True
                else:
                    latest_item = user_item
                    latest_is_guest = False
            else:
                latest_item = guest_item or user_item
                latest_is_guest = guest_item is not None

            candidates.append(
                {
                    "identity": identity,
                    "guest_item": guest_item,
                    "user_item": user_item,
                    "quantity": (guest_item.quantity if guest_item else 0)
                    + (user_item.quantity if user_item else 0),
                    "status": latest_item.status,
                    "updated": latest_item.updated,
                    "latest_is_guest": latest_is_guest,
                }
            )

        # A product with unavailable/zero stock cannot retain ACTIVE items.
        if not product.is_available or product.stock <= 0:
            for candidate in candidates:
                if candidate["status"] == CartItem.STATUS_ACTIVE:
                    candidate["status"] = CartItem.STATUS_SAVED
        else:
            active_candidates = [
                candidate
                for candidate in candidates
                if candidate["status"] == CartItem.STATUS_ACTIVE
            ]
            active_candidates.sort(
                key=lambda candidate: (
                    candidate["updated"],
                    1 if candidate["latest_is_guest"] else 0,
                    candidate["identity"][1] or 0,
                ),
                reverse=True,
            )

            remaining_stock = product.stock
            for candidate in active_candidates:
                allocated = min(candidate["quantity"], remaining_stock)
                if allocated <= 0:
                    candidate["status"] = CartItem.STATUS_SAVED
                else:
                    candidate["quantity"] = allocated
                    remaining_stock -= allocated

        for candidate in candidates:
            identity = candidate["identity"]
            guest_item = candidate["guest_item"]
            user_item = candidate["user_item"]
            final_quantity = candidate["quantity"]
            final_status = candidate["status"]
            variant_id = identity[1]
            variant = None
            if variant_id:
                variant = guest_item.variant if guest_item is not None else user_item.variant

            final_unit_price = _unit_price(product, variant)

            if user_item is not None:
                user_item.quantity = final_quantity
                user_item.status = final_status
                user_item.unit_price_snapshot = final_unit_price
                user_item.full_clean()
                user_item.save(
                    update_fields=[
                        "quantity",
                        "status",
                        "unit_price_snapshot",
                        "updated",
                    ]
                )
                if guest_item is not None:
                    guest_item.delete()
                continue

            if guest_item is not None:
                guest_item.cart = user_cart
                guest_item.quantity = final_quantity
                guest_item.status = final_status
                guest_item.unit_price_snapshot = final_unit_price
                guest_item.full_clean()
                guest_item.save(
                    update_fields=[
                        "cart",
                        "quantity",
                        "status",
                        "unit_price_snapshot",
                        "updated",
                    ]
                )

    user_cart.save(update_fields=["updated"])
    guest_cart.delete()
    request.session["cart_id"] = user_cart.pk
    request.session.modified = True
    return user_cart


@transaction.atomic
def add_product_to_cart(cart, product, quantity=1, variant=None):
    if quantity < 1:
        raise ValidationError("تعداد باید حداقل 1 باشد.")
    if not product.is_available:
        raise ValidationError("این محصول در حال حاضر قابل خرید نیست.")
    if quantity > product.stock:
        raise ValidationError("تعداد انتخاب‌شده بیشتر از موجودی محصول است.")

    _validate_variant(product, variant)
    item = (
        cart.items.select_for_update()
        .select_related("product", "variant")
        .filter(product=product, variant=variant)
        .first()
    )
    unit_price = _unit_price(product, variant)

    if item:
        new_quantity = item.quantity + quantity
        if new_quantity > product.stock:
            raise ValidationError("تعداد انتخاب‌شده بیشتر از موجودی محصول است.")
        item.quantity = new_quantity
        item.status = CartItem.STATUS_ACTIVE
        item.unit_price_snapshot = unit_price
        item.full_clean()
        item.save(update_fields=["quantity", "status", "unit_price_snapshot", "updated"])
        return item

    item = CartItem(
        cart=cart,
        product=product,
        variant=variant,
        quantity=quantity,
        unit_price_snapshot=unit_price,
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
        .select_related("product", "variant")
        .filter(product_id=product_id, status=CartItem.STATUS_ACTIVE)
        .first()
    )
    if not item:
        raise ValidationError("آیتم موردنظر پیدا نشد.")
    return update_cart_item_quantity_by_id(cart, item.id, quantity)


@transaction.atomic
def update_cart_item_quantity_by_id(cart, item_id, quantity):
    if quantity < 1:
        raise ValidationError("تعداد باید حداقل 1 باشد.")
    item = (
        cart.items.select_for_update()
        .select_related("product", "variant")
        .filter(pk=item_id, status=CartItem.STATUS_ACTIVE)
        .first()
    )
    if not item:
        raise ValidationError("آیتم موردنظر پیدا نشد.")
    if not item.product.is_available:
        raise ValidationError("این محصول در حال حاضر قابل خرید نیست.")
    if quantity > item.product.stock:
        raise ValidationError("تعداد انتخاب‌شده بیشتر از موجودی محصول است.")
    item.quantity = quantity
    item.unit_price_snapshot = _unit_price(item.product, item.variant)
    item.full_clean()
    item.save(update_fields=["quantity", "unit_price_snapshot", "updated"])
    return item


@transaction.atomic
def toggle_cart_item_status(cart, product_id, to_status):
    item = (
        cart.items.select_for_update()
        .select_related("product", "variant")
        .filter(product_id=product_id)
        .first()
    )
    if not item:
        raise ValidationError("آیتم موردنظر پیدا نشد.")
    return toggle_cart_item_status_by_id(cart, item.id, to_status)


@transaction.atomic
def toggle_cart_item_status_by_id(cart, item_id, to_status):
    if to_status not in {CartItem.STATUS_ACTIVE, CartItem.STATUS_SAVED}:
        raise ValidationError("وضعیت نامعتبر است.")
    item = (
        cart.items.select_for_update()
        .select_related("product", "variant")
        .filter(pk=item_id)
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
    item.unit_price_snapshot = _unit_price(item.product, item.variant)
    item.full_clean()
    item.save(update_fields=["status", "unit_price_snapshot", "updated"])
    return item


@transaction.atomic
def remove_product_from_cart(cart, product_id):
    item = cart.items.select_for_update().filter(product_id=product_id).first()
    if item:
        item.delete()


@transaction.atomic
def remove_cart_item(cart, item_id):
    item = cart.items.select_for_update().filter(pk=item_id).first()
    if item:
        item.delete()
