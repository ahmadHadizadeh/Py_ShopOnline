from django.db import transaction
from django.db.models import Avg, Count, Prefetch,Q
from django.shortcuts import get_object_or_404
from .models.product import Product
from .models.wishlist import Wishlist
from .models.review import Review

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


