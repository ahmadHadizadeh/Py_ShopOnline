from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from core.admin_formatting import (
    AdminDisplayFormattingMixin,
    format_jalali_datetime,
)
from .models.category import Category
from .models.product import Product
from .models.inventory import InventoryMovement
from .models.review import Review
from .models.variant import ProductVariant
from .models.wishlist import Wishlist

from .models.product_media import ProductImage, ProductSpecification
from .services import adjust_product_stock, record_physical_stock_count


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


class InventoryOperationForm(forms.Form):
    OPERATION_CHOICES = (
        ("increase", "افزایش موجودی (ورود کالا)"),
        ("decrease", "کاهش موجودی (خروج کالا یا کالای آسیب‌دیده)"),
        ("physical_count", "ثبت شمارش فیزیکی و اصلاح موجودی"),
    )

    operation = forms.ChoiceField(
        label="نوع عملیات",
        choices=OPERATION_CHOICES,
    )
    quantity = forms.IntegerField(
        label="تعداد",
        min_value=0,
        help_text=(
            "برای افزایش یا کاهش، تعداد جابه‌جاشده را وارد کنید؛ "
            "برای شمارش فیزیکی، عدد واقعی موجودی را بنویسید. "
            "عدد صفر فقط برای شمارش فیزیکی مجاز است."
        ),
    )
    reason = forms.CharField(
        label="دلیل عملیات",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self):
        cleaned_data = super().clean()
        operation = cleaned_data.get("operation")
        quantity = cleaned_data.get("quantity")
        if operation in {"increase", "decrease"} and quantity == 0:
            self.add_error(
                "quantity",
                "برای افزایش یا کاهش، تعداد باید بیشتر از صفر باشد.",
            )
        return cleaned_data


@admin.register(Product)
class ProductAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    money_display_fields = {"price", "old_price"}
    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/inventory/",
                self.admin_site.admin_view(self.inventory_view),
                name="catalog_product_inventory",
            )
        ]
        return custom_urls + super().get_urls()

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is not None and request.user.has_perm("catalog.change_product"):
            for index, (title, options) in enumerate(fieldsets):
                if title == "قیمت و موجودی":
                    updated_options = options.copy()
                    updated_options["fields"] = tuple(options["fields"]) + (
                        "inventory_management_link",
                    )
                    fieldsets[index] = (title, updated_options)
                    break
        return fieldsets

    @admin.display(description="عملیات انبار")
    def inventory_management_link(self, obj):
        if obj is None:
            return "پس از ثبت محصول، عملیات موجودی در دسترس خواهد بود."
        return format_html(
            '<a class="button" href="{}">مدیریت موجودی و ثبت شمارش فیزیکی</a>',
            reverse("admin:catalog_product_inventory", args=[obj.pk]),
        )

    def inventory_view(self, request, object_id):
        product = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_change_permission(request, product):
            raise PermissionDenied

        form = InventoryOperationForm(
            request.POST if request.method == "POST" else None
        )
        if request.method == "POST" and form.is_valid():
            operation = form.cleaned_data["operation"]
            quantity = form.cleaned_data["quantity"]
            reason = form.cleaned_data["reason"]
            try:
                if operation == "increase":
                    product, movement = adjust_product_stock(
                        product_id=product.pk,
                        delta=quantity,
                        reason=reason,
                        actor=request.user,
                    )
                elif operation == "decrease":
                    product, movement = adjust_product_stock(
                        product_id=product.pk,
                        delta=-quantity,
                        reason=reason,
                        actor=request.user,
                    )
                else:
                    product, movement = record_physical_stock_count(
                        product_id=product.pk,
                        physical_count=quantity,
                        reason=reason,
                        actor=request.user,
                    )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    (
                        f"گردش موجودی ثبت شد؛ موجودی از "
                        f"{movement.quantity_before} به {movement.quantity_after} رسید."
                    ),
                )
                return redirect(
                    "admin:catalog_product_inventory",
                    object_id=product.pk,
                )

        product.refresh_from_db()
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "bodyclass": "inventory-operation",
            "title": f"مدیریت موجودی: {product.name}",
            "product": product,
            "form": form,
            "change_url": reverse(
                "admin:catalog_product_change",
                args=[product.pk],
            ),
            "movements": list(
                product.inventory_movements.select_related("actor")[:20]
            ),
        }
        for movement in context["movements"]:
            movement.admin_created_at = format_jalali_datetime(
                movement.created_at,
                owner=movement,
            )

        return TemplateResponse(
            request,
            "admin/catalog/product/inventory.html",
            context,
        )

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.cart_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            super().save_model(request, obj, form, change)
            return

        # A product form may have been built from a stale row while a payment
        # callback reduces stock. Lock the current row and persist only fields
        # actually changed by this form; never write the stale stock snapshot.
        changed_fields = set(form.changed_data)
        concrete_fields = {field.name for field in obj._meta.concrete_fields}
        update_fields = (
            changed_fields & concrete_fields
        ) - {"stock", "is_available_status", "created", "updated"}

        with transaction.atomic():
            locked_product = Product.objects.select_for_update().get(pk=obj.pk)
            for field_name in update_fields:
                setattr(locked_product, field_name, getattr(obj, field_name))

            locked_product.update_availability_status_on_save()
            locked_product.save(
                update_fields=sorted(
                    update_fields | {"is_available_status", "updated"}
                )
            )

            # Keep the admin's in-memory object consistent for logging and
            # subsequent save_related processing.
            obj.stock = locked_product.stock
            obj.is_available_status = locked_product.is_available_status
            obj.updated = locked_product.updated

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
    list_filter = ("is_active", "category", "brand", AvailabilityFilter)
    search_fields = ("name", "name_en", "brand", "slug")
    list_select_related = ("category",)
    list_per_page = 25
    list_max_show_all = 200
    date_hierarchy = "updated"
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline, ProductSpecificationInline]
    ordering = ("-updated",)
    readonly_fields = ("stock", "is_available_status", "created", "updated", "inventory_management_link")
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


@admin.register(InventoryMovement)
class InventoryMovementAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    list_display = (
        "product_name",
        "movement_type_display",
        "delta_display",
        "quantity_before",
        "quantity_after",
        "actor_label",
        "order_number",
        "created_at",
    )
    list_filter = ("source", "created_at")
    search_fields = ("product_name", "reason", "order_number", "actor_label")
    list_select_related = ("product", "actor")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-pk")
    readonly_fields = (
        "product",
        "product_name",
        "delta",
        "quantity_before",
        "quantity_after",
        "source",
        "reason",
        "actor",
        "actor_label",
        "order_number",
        "created_at",
    )

    @admin.display(description="نوع عملیات", ordering="source")
    def movement_type_display(self, obj):
        return obj.get_source_display()

    @admin.display(description="تغییر موجودی", ordering="delta")
    def delta_display(self, obj):
        return f"{obj.delta:+d}"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductVariant)
class ProductVariantAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    money_display_fields = {"price_adjustment"}
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

    def get_deleted_objects(self, objs, request):
        category_ids = [obj.pk for obj in objs]
        if Category.objects.filter(
            pk__in=category_ids,
            products__isnull=False,
        ).exists():
            raise PermissionDenied(
                "دسته‌بندی دارای محصول را نمی‌توان حذف کرد؛ ابتدا محصولات را منتقل کنید."
            )
        return super().get_deleted_objects(objs, request)

    def delete_queryset(self, request, queryset):
        if queryset.filter(products__isnull=False).exists():
            raise PermissionDenied(
                "دسته‌بندی دارای محصول را نمی‌توان حذف کرد؛ ابتدا محصولات را منتقل کنید."
            )
        return super().delete_queryset(request, queryset)

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
class ReviewAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    date_display_fields = {"created_display": "created"}
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
