from django.contrib import admin
from django.db.models import Count, Q
from django.utils.html import format_html
from .models.category import Category
from .models.product import Product
from .models.review import Review
from .models.variant import ProductVariant
from .models.wishlist import Wishlist

from .models.product_media import ProductImage, ProductSpecification


# فیلتر سفارشی برای وضعیت موجودی
LOW_STOCK_THRESHOLD = 5


class AvailabilityFilter(admin.SimpleListFilter):
    title = "وضعیت موجودی"
    parameter_name = "availability"

    def lookups(self, request, model_admin):
        return [
            ("available", "موجود"),
            ("unavailable", "ناموجود یا غیرفعال"),
            ("out_of_stock", "اتمام موجودی (محصولات فعال)"),
            ("low_stock", f"کم‌موجودی (۱ تا {LOW_STOCK_THRESHOLD})"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "available":
            return queryset.filter(
                is_active=True,
                is_available_status=True,
                stock__gt=0,
            )
        if self.value() == "unavailable":
            return queryset.filter(
                Q(is_active=False)
                | Q(is_available_status=False)
                | Q(stock=0)
            )
        if self.value() == "out_of_stock":
            return queryset.filter(is_active=True, stock=0)
        if self.value() == "low_stock":
            return queryset.filter(
                is_active=True,
                is_available_status=True,
                stock__gt=0,
                stock__lte=LOW_STOCK_THRESHOLD,
            )
        return queryset


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductSpecificationInline(admin.TabularInline):
    model = ProductSpecification
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.cart_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        field_labels = {
            "category": "دسته‌بندی",
            "image": "تصویر اصلی محصول",
            "slug": "نشانی محصول",
        }
        field_help_texts = {
            "slug": "نشانی یکتای محصول در فروشگاه؛ این مقدار به‌صورت خودکار از نام محصول پیشنهاد می‌شود.",
            "related_products": (
                "برای انتخاب چند محصول، هنگام انتخاب موارد کلید Ctrl در ویندوز "
                "یا Command در macOS را نگه دارید."
            ),
        }
        for field_name, label in field_labels.items():
            if field_name in form.base_fields:
                form.base_fields[field_name].label = label
        for field_name, help_text in field_help_texts.items():
            if field_name in form.base_fields:
                form.base_fields[field_name].help_text = help_text
        return form

    @admin.display(description="دسته‌بندی", ordering="category__name")
    def category_display(self, obj):
        return obj.category.name

    # Keep common stock/pricing controls visible in the list; availability itself is derived.
    list_display = (
        "name",
        "category_display",
        "brand",
        "price",
        "stock",
        "availability_label",
        "is_active",
        "updated",
    )
    list_editable = ("price", "stock")
    list_filter = ("is_active", "category", "brand", AvailabilityFilter)
    search_fields = ("name", "name_en", "brand", "slug")
    list_select_related = ("category",)
    list_per_page = 25
    list_max_show_all = 200
    date_hierarchy = "updated"
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline, ProductSpecificationInline]
    ordering = ("-updated",)
    readonly_fields = ("is_available_status", "created", "updated")
    fieldsets = (
        (
            "اطلاعات اصلی محصول",
            {"fields": ("name", "name_en", "brand", "category", "image")},
        ),
        (
            "قیمت و موجودی",
            {
                "fields": (
                    "price",
                    "old_price",
                    "stock",
                    "is_active",
                    "is_available_status",
                )
            },
        ),
        (
            "محتوای صفحه محصول",
            {"fields": ("description", "highlights", "review")},
        ),
        (
            "ارسال و گارانتی",
            {"fields": ("guarantee", "delivery_info")},
        ),
        (
            "تنظیمات تکمیلی",
            {"fields": ("slug", "related_products", "created", "updated")},
        ),
    )

    @admin.display(description="وضعیت فروش", ordering="is_available_status")
    def availability_label(self, obj):
        if not obj.is_active:
            tone, label = "muted", "غیرفعال"
        elif obj.is_available:
            tone, label = "success", "قابل فروش"
        elif obj.stock == 0:
            tone, label = "danger", "اتمام موجودی"
        else:
            tone, label = "warning", "ناموجود"

        return format_html(
            '<span class="store-status-pill store-status-pill--{}">{}</span>',
            tone,
            label,
        )


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.cart_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    list_display = ("product", "name", "value", "price_adjustment")
    list_filter = ("name",)
    search_fields = ("product__name", "name", "value")
    list_select_related = ("product",)
    list_per_page = 25
    autocomplete_fields = ("product",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.products.exists():
            return False
        return super().has_delete_permission(request, obj)

    list_display = ("name", "parent", "is_active", "slug")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug")
    list_select_related = ("parent",)
    list_per_page = 25
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (
            "اطلاعات دسته‌بندی",
            {
                "fields": ("name", "parent", "is_active"),
                "description": (
                    "دسته‌بندی دارای محصول قابل حذف نیست. برای توقف نمایش آن، "
                    "وضعیت فعال را خاموش کنید یا ابتدا محصولات را به دسته دیگری منتقل کنید."
                ),
            },
        ),
        (
            "نشانی دسته‌بندی",
            {
                "fields": ("slug",),
                "description": "نشانی از نام دسته‌بندی ساخته می‌شود؛ در صورت نیاز می‌توانید آن را اصلاح کنید.",
            },
        ),
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "is_approved", "created_display")
    list_filter = ("is_approved", "created")
    list_editable = ("is_approved",)
    search_fields = ("product__name", "user__username", "user__email", "comment")
    list_select_related = ("product", "user")
    list_per_page = 25
    ordering = ("-created",)

    @admin.display(description="تاریخ ثبت", ordering="created")
    def created_display(self, obj):
        return obj.created.strftime("%Y/%m/%d %H:%M")



@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ("user_display", "product_count")
    list_display_links = ("user_display",)
    search_fields = (
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
        "products__name",
    )
    list_select_related = ("user",)
    list_per_page = 25
    fields = ("user", "products_display")
    readonly_fields = fields
    ordering = ("user__username",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .prefetch_related("products")
            .annotate(_product_count=Count("products", distinct=True))
        )

    @admin.display(description="مشتری", ordering="user__username")
    def user_display(self, obj):
        return obj.user.get_full_name() or obj.user.email or obj.user.get_username()

    @admin.display(description="تعداد علاقه‌مندی‌ها")
    def product_count(self, obj):
        return obj._product_count

    @admin.display(description="محصولات مورد علاقه")
    def products_display(self, obj):
        products = list(obj.products.all())
        names = [product.name for product in products[:30]]
        remaining = len(products) - len(names)
        if remaining:
            names.append(f"… و {remaining} محصول دیگر")
        return "، ".join(names) or "علاقه‌مندی ثبت نشده است"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
