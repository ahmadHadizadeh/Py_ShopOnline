from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import (
    Avg,
    Count,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
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


PRODUCT_LIST_SORT_OPTIONS = {
    "newest": ("-created", "-pk"),
    "price_asc": ("price", "-created", "-pk"),
    "price_desc": ("-price", "-created", "-pk"),
}
PRODUCTS_PER_PAGE = 12
HOMEPAGE_SHOWCASE_LIMIT = 8


def _with_approved_review_summary(queryset):
    return queryset.annotate(
        avg_rating=Avg("reviews__rating", filter=Q(reviews__is_approved=True)),
        review_count=Count(
            "reviews",
            filter=Q(reviews__is_approved=True),
            distinct=True,
        ),
    )


def _filter_products_by_search(queryset, search_query):
    if not search_query:
        return queryset
    return queryset.filter(
        Q(name__icontains=search_query)
        | Q(name_en__icontains=search_query)
        | Q(brand__icontains=search_query)
        | Q(description__icontains=search_query)
        | Q(highlights__icontains=search_query)
    )


@require_GET
def search_suggestions(request):
    search_query = request.GET.get("q", "").strip()[:120]
    if len(search_query) < 2:
        return JsonResponse({"results": []})

    products = Product.objects.filter(is_active=True).select_related("category")
    products = _filter_products_by_search(products, search_query)

    category_slug = request.GET.get("category", "").strip()
    if category_slug:
        products = products.filter(
            category__slug=category_slug,
            category__is_active=True,
        )

    suggestions = products.order_by("-created", "-pk").values(
        "name",
        "slug",
        "price",
        "category__name",
    )[:6]

    return JsonResponse(
        {
            "results": [
                {
                    "name": item["name"],
                    "url": reverse(
                        "catalog:product_detail",
                        kwargs={"slug": item["slug"]},
                    ),
                    "price": str(item["price"]),
                    "category": item["category__name"],
                }
                for item in suggestions
            ]
        }
    )


def home(request, category_slug=None):
    category = None
    products = Product.objects.filter(is_active=True).select_related("category")

    if category_slug:
        category = get_object_or_404(
            Category,
            slug=category_slug,
            is_active=True,
        )
        products = products.filter(category=category)

    search_query = request.GET.get("q", "").strip()[:120]
    products = _filter_products_by_search(products, search_query)

    sort = request.GET.get("sort", "newest")
    sort_fields = PRODUCT_LIST_SORT_OPTIONS.get(sort)
    if sort_fields is None:
        sort = "newest"
        sort_fields = PRODUCT_LIST_SORT_OPTIONS[sort]

    products = products.order_by(*sort_fields)
    paginator = Paginator(products, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    # Keep the paginator count query free of review joins; aggregate ratings
    # only for the 12 products actually rendered on this page.
    page_obj.object_list = _with_approved_review_summary(page_obj.object_list)

    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_params["sort"] = sort
    if search_query:
        query_params["q"] = search_query
    else:
        query_params.pop("q", None)

    best_selling_products = []
    special_offer_products = []

    if category is None and not search_query:
        # Count only completed sales. A correlated subquery keeps the sales
        # aggregate independent from the review join used by the product cards.
        from orders.models import Order, OrderItem, Payment

        paid_item_totals = (
            OrderItem.objects.filter(
                product_id=OuterRef("pk"),
                order__status__in=(
                    Order.Status.PAID,
                    Order.Status.PROCESSING,
                    Order.Status.COMPLETED,
                ),
                order__payment__status=Payment.Status.SUCCESS,
                order__stock_reduced=True,
            )
            .order_by()
            .values("product_id")
            .annotate(total_sold=Sum("quantity"))
            .values("total_sold")[:1]
        )

        best_selling_queryset = (
            Product.objects.filter(
                is_active=True,
                is_available_status=True,
                stock__gt=0,
            )
            .select_related("category")
            .annotate(
                sold_quantity=Coalesce(
                    Subquery(paid_item_totals, output_field=IntegerField()),
                    Value(0),
                    output_field=IntegerField(),
                )
            )
            .filter(sold_quantity__gt=0)
            .order_by("-sold_quantity", "-pk")
        )
        best_selling_products = _with_approved_review_summary(
            best_selling_queryset
        )[:HOMEPAGE_SHOWCASE_LIMIT]

        special_offer_queryset = Product.objects.filter(
            is_active=True,
            is_available_status=True,
            stock__gt=0,
            old_price__gt=F("price"),
        ).select_related("category")
        special_offer_products = _with_approved_review_summary(
            special_offer_queryset.order_by("-created", "-pk")
        )[:HOMEPAGE_SHOWCASE_LIMIT]

    context = {
        "category": category,
        "products": products,
        "page_obj": page_obj,
        "search_query": search_query,
        "sort": sort,
        "pagination_query": query_params.urlencode(),
        "best_selling_products": best_selling_products,
        "special_offer_products": special_offer_products,
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
