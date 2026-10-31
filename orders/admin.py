from django.contrib import admin
from django.contrib import messages
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db import transaction
from django.utils.html import format_html

from .models import Order, OrderAddressSnapshot, OrderItem, Payment, ShippingMethod
from .services import OrderService


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    show_change_link = True
    readonly_fields = (
        "product",
        "variant_id",
        "product_name",
        "variant_name",
        "sku",
        "quantity",
        "unit_price",
        "subtotal_price",
        "created",
        "updated",
    )

    def has_add_permission(self, request, obj=None):
        return False


class OrderAddressSnapshotInline(admin.StackedInline):
    model = OrderAddressSnapshot
    extra = 0
    can_delete = False
    readonly_fields = (
        "recipient_name",
        "recipient_mobile",
        "postal_code",
        "province",
        "city",
        "address_line",
        "created",
        "updated",
    )

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.StackedInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = (
        "user",
        "amount",
        "status",
        "gateway_name",
        "transaction_code",
        "reference_code",
        "gateway_response",
        "created",
        "updated",
    )

    def has_add_permission(self, request, obj=None):
        return False


def _transition_orders_with_audit_log(modeladmin, request, queryset, new_status):
    changed = 0
    for selected_order in queryset:
        try:
            # Keep the state transition and its audit record in one transaction.
            with transaction.atomic():
                previous_status = selected_order.get_status_display()
                updated_order = OrderService.transition_status(
                    order_id=selected_order.pk,
                    new_status=new_status,
                )
                modeladmin.log_change(
                    request,
                    updated_order,
                    (
                        f"وضعیت سفارش از «{previous_status}» به "
                        f"«{updated_order.get_status_display()}» "
                        "از طریق عملیات گروهی تغییر کرد."
                    ),
                )
            changed += 1
        except Exception as exc:
            modeladmin.message_user(request, str(exc), level=messages.ERROR)
    modeladmin.message_user(request, f"{changed} سفارش به وضعیت جدید منتقل شد.")


@admin.action(permissions=["change"])
def move_paid_orders_to_processing(modeladmin, request, queryset):
    _transition_orders_with_audit_log(
        modeladmin,
        request,
        queryset,
        Order.Status.PROCESSING,
    )


move_paid_orders_to_processing.short_description = "انتقال سفارش‌های پرداخت‌شده به در حال پردازش"


@admin.action(permissions=["change"])
def move_processing_orders_to_completed(modeladmin, request, queryset):
    _transition_orders_with_audit_log(
        modeladmin,
        request,
        queryset,
        Order.Status.COMPLETED,
    )


move_processing_orders_to_completed.short_description = "تکمیل سفارش‌های در حال پردازش"


@admin.action(permissions=["change"])
def cancel_unpaid_orders(modeladmin, request, queryset):
    _transition_orders_with_audit_log(
        modeladmin,
        request,
        queryset,
        Order.Status.CANCELLED,
    )


cancel_unpaid_orders.short_description = "لغو سفارش‌های پرداخت‌نشده"

class StockReductionFilter(admin.SimpleListFilter):
    title = "وضعیت کاهش موجودی"
    parameter_name = "stock_reduced"

    def lookups(self, request, model_admin):
        return (
            ("yes", "کاهش موجودی ثبت شده"),
            ("no", "کاهش موجودی ثبت نشده"),
        )

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(stock_reduced=True)
        if self.value() == "no":
            return queryset.filter(stock_reduced=False)
        return queryset


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_per_page = 25
    date_hierarchy = "created"
    list_display = (
        "order_number",
        "recipient_name_display",
        "recipient_mobile_display",
        "user_display",
        "get_status_display_value",
        "get_final_amount_display",
        "get_payment_status_display",
        "stock_reduced_display",
        "created",
    )
    list_filter = (
        "status",
        StockReductionFilter,
        "shipping_method",
        "payment__status",
        "created",
    )
    search_fields = (
        "order_number",
        "user__username",
        "user__email",
        "address_snapshot__recipient_name",
        "address_snapshot__recipient_mobile",
        "payment__transaction_code",
        "payment__reference_code",
    )
    ordering = ("-created",)
    list_select_related = ("user", "shipping_method", "payment", "address_snapshot")
    readonly_fields = (
        "order_number",
        "user",
        "cart",
        "shipping_method",
        "status",
        "subtotal_amount",
        "discount_amount",
        "shipping_amount",
        "final_amount",
        "paid_at",
        "cancelled_at",
        "completed_at",
        "customer_note",
        "stock_reduced",
        "stock_reduced_display",
        "created",
        "updated",
    )
    fieldsets = (
        (
            "اطلاعات سفارش",
            {
                "fields": (
                    "order_number",
                    "user",
                    "cart",
                    "shipping_method",
                    "status",
                    "stock_reduced_display",
                )
            },
        ),
        (
            "مبالغ",
            {
                "fields": (
                    "subtotal_amount",
                    "discount_amount",
                    "shipping_amount",
                    "final_amount",
                )
            },
        ),
        (
            "یادداشت‌ها",
            {
                "fields": ("customer_note", "admin_note"),
            },
        ),
        (
            "زمان‌ها",
            {
                "fields": (
                    "paid_at",
                    "cancelled_at",
                    "completed_at",
                    "created",
                    "updated",
                )
            },
        ),
    )
    inlines = (
        OrderItemInline,
        OrderAddressSnapshotInline,
        PaymentInline,
    )
    actions = (
        move_paid_orders_to_processing,
        move_processing_orders_to_completed,
        cancel_unpaid_orders,
    )

    @staticmethod
    def _can_view_payment_data(request):
        user = request.user
        return bool(
            user.is_superuser
            or user.has_perm("orders.view_payment")
            or user.has_perm("orders.change_payment")
        )

    def get_list_display(self, request):
        fields = list(super().get_list_display(request))
        if not self._can_view_payment_data(request):
            fields = [
                field for field in fields
                if field != "get_payment_status_display"
            ]
        return fields

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if not self._can_view_payment_data(request):
            filters = [item for item in filters if item != "payment__status"]
        return filters

    def get_search_fields(self, request):
        fields = tuple(super().get_search_fields(request))
        if not self._can_view_payment_data(request):
            fields = tuple(
                field for field in fields
                if not field.startswith("payment__")
            )
        return fields

    def get_list_select_related(self, request):
        relations = super().get_list_select_related(request)
        if self._can_view_payment_data(request):
            return relations
        if isinstance(relations, (tuple, list)):
            return tuple(relation for relation in relations if relation != "payment")
        return relations

    def lookup_allowed(self, lookup, value, request):
        if (
            not self._can_view_payment_data(request)
            and (lookup == "payment" or lookup.startswith("payment__"))
        ):
            return False
        return super().lookup_allowed(lookup, value, request)

    @admin.display(description="نام گیرنده", ordering="address_snapshot__recipient_name")
    def recipient_name_display(self, obj):
        try:
            return obj.address_snapshot.recipient_name
        except OrderAddressSnapshot.DoesNotExist:
            return "—"

    @admin.display(description="موبایل گیرنده", ordering="address_snapshot__recipient_mobile")
    def recipient_mobile_display(self, obj):
        try:
            return obj.address_snapshot.recipient_mobile
        except OrderAddressSnapshot.DoesNotExist:
            return "—"

    @admin.display(description="کاربر", ordering="user__email")
    def user_display(self, obj):
        return obj.user.email or obj.user.username

    @admin.display(description="وضعیت سفارش", ordering="status")
    def get_status_display_value(self, obj):
        return format_html(
            '<span class="store-status-pill store-status-pill--order-{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    @admin.display(description="مبلغ نهایی", ordering="final_amount")
    def get_final_amount_display(self, obj):
        return f"{intcomma(int(obj.final_amount))} تومان"

    @admin.display(description="وضعیت پرداخت", ordering="payment__status")
    def get_payment_status_display(self, obj):
        try:
            payment = obj.payment
        except Payment.DoesNotExist:
            return format_html(
                '<span class="store-status-pill store-status-pill--muted">{}</span>',
                "بدون پرداخت",
            )

        return format_html(
            '<span class="store-status-pill store-status-pill--payment-{}">{}</span>',
            payment.status,
            payment.get_status_display(),
        )

    @admin.display(description="وضعیت کاهش موجودی", ordering="stock_reduced")
    def stock_reduced_display(self, obj):
        if obj.stock_reduced:
            tone, label = "success", "ثبت شده"
        else:
            tone, label = "warning", "ثبت نشده"
        return format_html(
            '<span class="store-status-pill store-status-pill--{}">{}</span>',
            tone,
            label,
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_per_page = 25
    list_display = (
        "order",
        "product_name",
        "variant_name",
        "quantity",
        "unit_price",
        "subtotal_price",
        "created",
    )
    list_filter = ("created", "updated")
    search_fields = (
        "order__order_number",
        "product_name",
        "variant_name",
        "sku",
    )
    list_select_related = ("order", "product")
    readonly_fields = (
        "order",
        "product",
        "variant_id",
        "product_name",
        "variant_name",
        "sku",
        "quantity",
        "unit_price",
        "subtotal_price",
        "created",
        "updated",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_per_page = 25
    date_hierarchy = "created"
    list_display = (
        "order",
        "user",
        "amount_display",
        "status_badge",
        "gateway_name",
        "transaction_code",
        "reference_code",
        "created",
    )
    list_filter = ("status", "gateway_name", "created")
    search_fields = (
        "order__order_number",
        "user__username",
        "user__email",
        "transaction_code",
        "reference_code",
    )
    ordering = ("-created",)
    list_select_related = ("order", "user")
    readonly_fields = (
        "order",
        "user",
        "amount",
        "status",
        "gateway_name",
        "transaction_code",
        "reference_code",
        "gateway_response",
        "created",
        "updated",
    )
    fieldsets = (
        (
            "پرداخت",
            {
                "fields": (
                    "order",
                    "user",
                    "amount",
                    "status",
                    "gateway_name",
                )
            },
        ),
        (
            "شناسه‌های تراکنش",
            {
                "fields": (
                    "transaction_code",
                    "reference_code",
                )
            },
        ),
        (
            "پاسخ درگاه",
            {
                "fields": ("gateway_response",),
            },
        ),
        (
            "زمان‌ها",
            {
                "fields": ("created", "updated"),
            },
        ),
    )

    @admin.display(description="مبلغ پرداخت", ordering="amount")
    def amount_display(self, obj):
        return f"{intcomma(int(obj.amount))} تومان"

    @admin.display(description="وضعیت پرداخت", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="store-status-pill store-status-pill--payment-{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderAddressSnapshot)
class OrderAddressSnapshotAdmin(admin.ModelAdmin):
    list_per_page = 25
    date_hierarchy = "created"
    list_display = (
        "order",
        "recipient_name",
        "recipient_mobile",
        "province",
        "city",
        "created",
    )
    search_fields = (
        "order__order_number",
        "recipient_name",
        "recipient_mobile",
        "postal_code",
        "province",
        "city",
    )
    list_select_related = ("order",)
    readonly_fields = (
        "order",
        "recipient_name",
        "recipient_mobile",
        "postal_code",
        "province",
        "city",
        "address_line",
        "created",
        "updated",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.orders.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.orders.exists():
            fields.extend(
                (
                    "name",
                    "description",
                    "cost",
                    "free_shipping_threshold",
                    "estimated_delivery_days",
                )
            )
        return tuple(fields)

    list_per_page = 25
    date_hierarchy = "updated"
    list_display = (
        "name",
        "get_cost_display",
        "get_threshold_display",
        "estimated_delivery_days",
        "is_active",
        "updated",
    )
    list_filter = ("is_active", "estimated_delivery_days")
    search_fields = ("name", "description")
    list_editable = ("is_active",)
    ordering = ("cost",)
    readonly_fields = ("created", "updated")
    fieldsets = (
        (
            "اطلاعات اصلی",
            {
                "fields": ("name", "description", "is_active"),
                "description": (
                    "پس از استفاده در سفارش ثبت‌شده، مشخصات این روش ارسال قفل می‌شود. "
                    "برای نرخ یا شرایط جدید، روش ارسال تازه‌ای بسازید و این مورد را غیرفعال کنید."
                ),
            },
        ),
        (
            "تنظیمات هزینه",
            {
                "fields": ("cost", "free_shipping_threshold"),
                "description": "مبالغ را به واحد پول اصلی سیستم (تومان) وارد کنید.",
            },
        ),
        (
            "زمان‌بندی و سیستم",
            {
                "fields": ("estimated_delivery_days", "created", "updated"),
            },
        ),
    )

    @admin.display(description="هزینه ارسال (تومان)")
    def get_cost_display(self, obj):
        return f"{intcomma(int(obj.cost))} تومان"

    @admin.display(description="حداقل خرید برای ارسال رایگان")
    def get_threshold_display(self, obj):
        if obj.free_shipping_threshold:
            return f"{intcomma(int(obj.free_shipping_threshold))} تومان"
        return "بدون سقف"
