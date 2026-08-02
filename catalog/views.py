from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.views.decorators.http import require_http_methods, require_POST
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from catalog.models.category import Category
from catalog.models.product import Product
from catalog.services import (
    get_product_detail_data,
    get_related_products,
    get_user_wishlist_status,
    save_product_review,
    toggle_wishlist,
)


def home(request, category_slug=None):
    category = None
    categories = Category.objects.filter(is_active=True)
    products = Product.objects.filter(is_active=True).select_related("category")

    if category_slug:
        category = get_object_or_404(
            Category,
            slug=category_slug,
            is_active=True,
        )
        products = products.filter(category=category)

    context = {
        "category": category,
        "categories": categories,
        "products": products,
    }
    return render(request, "catalog/home.html", context)


@require_http_methods(["GET", "POST"])
def product_detail(request, slug):
    # ابتدا محصول را با نظرات تایید شده دریافت می‌کنیم
    product = get_product_detail_data(slug)

    if request.method == "POST":
        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

        if not request.user.is_authenticated:
            if is_ajax:
                return JsonResponse(
                    {"success": False, "message": "لطفاً ابتدا وارد شوید."},
                    status=401,
                )
            return redirect_to_login(request.get_full_path())

        try:
            # --- بخش امنیت و پاکسازی ورودی‌ها ---
            raw_rating = request.POST.get("rating")
            raw_comment = request.POST.get("comment", "")

            # پاکسازی متن از تگ‌های مخرب (XSS Protection)
            safe_comment = strip_tags(raw_comment).strip()

            # بررسی معتبر بودن امتیاز (Value Protection)
            try:
                safe_rating = int(raw_rating)
                if not (1 <= safe_rating <= 5):
                    raise ValueError("امتیاز باید بین ۱ تا ۵ باشد.")
            except (TypeError, ValueError):
                raise ValueError("امتیاز وارد شده معتبر نیست.")

            if not safe_comment:
                raise ValueError("متن نظر نمی‌تواند خالی باشد.")
            # ----------------------------------

            # ثبت نظر با مقدار is_approved=False
            _, created = save_product_review(
                user=request.user,
                product=product,
                rating=safe_rating,
                comment=safe_comment,
            )

            msg = (
                "نظر شما با موفقیت ثبت شد. پس از تأیید مدیر نمایش داده خواهد شد."
                if created
                else "نظر شما با موفقیت ویرایش شد. پس از تأیید مدیر نمایش داده خواهد شد."
            )

            if is_ajax:
                # دوباره محصول را با نظرات تایید شده دریافت می‌کنیم تا برای رندر کردن آماده باشد
                product = get_product_detail_data(slug)
                reviews_html = render_to_string(
                    "catalog/partials/review_list.html",
                    {
                        "product": product,
                        "request": request,
                    },  # product.approved_reviews استفاده شود
                )
                return JsonResponse(
                    {
                        "success": True,
                        "message": msg,
                        "reviews_html": reviews_html,
                        "review_count": product.review_count,
                    }
                )

            messages.success(request, msg)
            return redirect("catalog:product_detail", slug=product.slug)

        except ValueError as error:
            if is_ajax:
                return JsonResponse(
                    {"success": False, "message": str(error)},
                    status=400,
                )
            messages.error(request, str(error))
            return redirect("catalog:product_detail", slug=product.slug)

    # برای نمایش اولیه محصول، product در context موجود است
    context = {
        "product": product,
        "related_products": get_related_products(product),
        "is_wishlisted": get_user_wishlist_status(request.user, product),
        "login_url": resolve_url(settings.LOGIN_URL),
    }
    return render(request, "catalog/details.html", context)


@require_POST
def wishlist_toggle_view(request, slug):
    if not request.user.is_authenticated:
        return JsonResponse(
            {
                "success": False,
                "message": "برای افزودن به علاقه مندی ها ابتدا وارد شوید.",
            },
            status=401,
        )

    product = get_object_or_404(Product, slug=slug, is_active=True)
    is_added, _ = toggle_wishlist(request.user, product.id)

    return JsonResponse(
        {
            "success": True,
            "is_wishlisted": is_added,
            "message": (
                "محصول به علاقه مندی ها اضافه شد."
                if is_added
                else "محصول از علاقه مندی ها حذف شد."
            ),
        }
    )
