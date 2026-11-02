from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Prefetch,Q
from django.shortcuts import get_object_or_404
from .models.product import Product
from .models.inventory import InventoryMovement
from .models.wishlist import Wishlist
from .models.review import Review


MAX_PRODUCT_STOCK = 2_147_483_647
MAX_INVENTORY_REASON_LENGTH = 500


def _validate_inventory_integer(value, field_label, *, allow_zero):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field_label} باید عدد صحیح باشد.")
    if value < 0 or (not allow_zero and value == 0):
        qualifier = "صفر یا بیشتر" if allow_zero else "بزرگ‌تر از صفر"
        raise ValidationError(f"{field_label} باید {qualifier} باشد.")


def _validate_inventory_actor(actor):
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or not actor.has_perm("catalog.change_product")
    ):
        raise PermissionDenied("مجوز لازم برای مدیریت موجودی محصول را ندارید.")


def _validate_inventory_reason(reason):
    normalized = str(reason or "").strip()
    if not normalized:
        raise ValidationError("ثبت دلیل عملیات موجودی الزامی است.")
    if len(normalized) > MAX_INVENTORY_REASON_LENGTH:
        raise ValidationError(
            f"دلیل عملیات نباید بیشتر از {MAX_INVENTORY_REASON_LENGTH} نویسه باشد."
        )
    return normalized


def _record_locked_inventory_movement(
    *,
    product,
    delta,
    source,
    reason,
    actor=None,
    order_number="",
):
    """Apply and audit a stock change while the caller holds the Product row lock."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Inventory movements must be recorded inside a transaction.")

    quantity_before = product.stock
    quantity_after = quantity_before + delta
    if quantity_after < 0:
        raise ValidationError(
            f"موجودی کافی نیست؛ موجودی فعلی {quantity_before} عدد است."
        )
    if quantity_after > MAX_PRODUCT_STOCK:
        raise ValidationError("موجودی از محدودهٔ قابل‌ذخیره‌سازی بیشتر است.")

    product.stock = quantity_after
    product.update_availability_status_on_save()
    product.save(update_fields=["stock", "is_available_status", "updated"])

    actor_label = "سامانه پرداخت"
    if actor is not None:
        actor_label = actor.get_full_name().strip() or actor.get_username()

    movement = InventoryMovement.objects.create(
        product=product,
        product_name=product.name,
        delta=delta,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        source=source,
        reason=reason,
        actor=actor,
        actor_label=actor_label[:150],
        order_number=order_number or "",
    )
    return product, movement


@transaction.atomic
def adjust_product_stock(*, product_id, delta, reason, actor):
    """Increase or decrease stock by a relative quantity under a row lock."""
    _validate_inventory_actor(actor)
    if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
        raise ValidationError("مقدار افزایش یا کاهش باید عدد صحیح و غیرصفر باشد.")
    reason = _validate_inventory_reason(reason)

    try:
        product = Product.objects.select_for_update().get(pk=product_id)
    except Product.DoesNotExist:
        raise ValidationError("محصول موردنظر پیدا نشد.") from None

    return _record_locked_inventory_movement(
        product=product,
        delta=delta,
        source=InventoryMovement.Source.MANUAL_ADJUSTMENT,
        reason=reason,
        actor=actor,
    )


@transaction.atomic
def record_physical_stock_count(*, product_id, physical_count, reason, actor):
    """Reconcile the system quantity to a physical count, preserving its delta."""
    _validate_inventory_actor(actor)
    _validate_inventory_integer(
        physical_count,
        "تعداد شمارش‌شده",
        allow_zero=True,
    )
    reason = _validate_inventory_reason(reason)

    try:
        product = Product.objects.select_for_update().get(pk=product_id)
    except Product.DoesNotExist:
        raise ValidationError("محصول موردنظر پیدا نشد.") from None

    delta = physical_count - product.stock
    return _record_locked_inventory_movement(
        product=product,
        delta=delta,
        source=InventoryMovement.Source.PHYSICAL_COUNT,
        reason=reason,
        actor=actor,
    )

@transaction.atomic
def save_product_review(user, product, rating, comment):
    if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
        raise ValueError("برای ثبت نظر ابتدا وارد حساب کاربری شوید.")

    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ValueError("امتیاز انتخاب شده معتبر نیست.")

    comment = str(comment or "").strip()

    if not (1 <= rating <= 5):
        raise ValueError("امتیاز باید بین 1 تا 5 باشد.")
    if len(comment) < 3:
        raise ValueError("متن نظر باید حداقل 3 کاراکتر باشد.")
    if len(comment) > 2000:
        raise ValueError("متن نظر نمی‌تواند بیشتر از 2000 کاراکتر باشد.")

    review, created = Review.objects.update_or_create(
        user=user,
        product=product,
        defaults={"rating": rating, "comment": comment,"is_approved": False,},
    )
    return review, created


def get_product_detail_data(product_slug):
    related_products_qs = Product.objects.filter(is_active=True).select_related("category")
    # اینجا QuerySet برای نظرات تایید شده را تعریف می‌کنیم
    approved_reviews_qs = Review.objects.filter(is_approved=True).select_related("user")

    return get_object_or_404(
        Product.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related(
            "variants",
            # حالا Prefetch را با to_attr="approved_reviews" استفاده می‌کنیم
            Prefetch("reviews", queryset=approved_reviews_qs, to_attr="approved_reviews"),
            "images",
            "specs",
            Prefetch("related_products", queryset=related_products_qs, to_attr="active_related_products"),
        )
        .annotate(
            # برای محاسبه میانگین امتیاز، همچنان باید فیلتر is_approved=True را اعمال کنیم
            avg_rating=Avg("reviews__rating", filter=Q(reviews__is_approved=True)),
            # و برای شمارش نظرات تایید شده
            review_count=Count("reviews", filter=Q(reviews__is_approved=True), distinct=True),
        ),
        slug=product_slug,
    )

def get_related_products(product, limit=4):
    explicit_related = list(getattr(product, "active_related_products", []))
    if explicit_related:
        return explicit_related[:limit]

    if not product.category_id:
        return []

    return list(
        Product.objects.filter(is_active=True, category_id=product.category_id)
        .exclude(pk=product.pk)
        .select_related("category")
        .order_by("-id")[:limit]
    )

def get_user_wishlist_status(user, product):
    if not getattr(user, "is_authenticated", False):
        return False
    wishlist = Wishlist.objects.filter(user=user).first()
    return wishlist.products.filter(pk=product.pk).exists() if wishlist else False

@transaction.atomic
def toggle_wishlist(user, product_id):
    if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
        raise ValueError("کاربر احراز هویت نشده است.")

    product = get_object_or_404(Product, pk=product_id, is_active=True)
    wishlist, _ = Wishlist.objects.get_or_create(user=user)

    if wishlist.products.filter(pk=product.pk).exists():
        wishlist.products.remove(product)
        is_added = False
    else:
        wishlist.products.add(product)
        is_added = True
    
    return is_added, product


