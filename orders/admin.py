from django.contrib import admin
from django.contrib import messages
from django.contrib.humanize.templatetags.humanize import intcomma

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


def move_paid_orders_to_processing(modeladmin, request, queryset):
    changed = 0
    for order in queryset:
        try:
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.PROCESSING,
            )
            changed += 1
        except Exception as exc:
            modeladmin.message_user(request, str(exc), level=messages.ERROR)
    modeladmin.message_user(request, f"{changed} سفارش به وضعیت در حال پردازش منتقل شد.")


move_paid_orders_to_processing.short_description = "انتقال سفارش‌های پرداخت‌شده به در حال پردازش"


def move_processing_orders_to_completed(modeladmin, request, queryset):
    changed = 0
    for order in queryset:
        try:
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.COMPLETED,
            )
            changed += 1
        except Exception as exc:
            modeladmin.message_user(request, str(exc), level=messages.ERROR)
    modeladmin.message_user(request, f"{changed} سفارش به وضعیت تکمیل‌شده منتقل شد.")


move_processing_orders_to_completed.short_description = "تکمیل سفارش‌های در حال پردازش"


def cancel_unpaid_orders(modeladmin, request, queryset):
    changed = 0
    for order in queryset:
        try:
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.CANCELLED,
            )
            changed += 1
        except Exception as exc:
            modeladmin.message_user(request, str(exc), level=messages.ERROR)
    modeladmin.message_user(request, f"{changed} سفارش لغو شد.")


cancel_unpaid_orders.short_description = "لغو سفارش‌های پرداخت‌نشده"


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "user_display",
        "get_status_display_value",
        "get_final_amount_display",
        "get_payment_status_display",
        "stock_reduced",
        "created",
    )
    list_filter = (
        "status",
        "stock_reduced",
        "shipping_method",
        "payment__status",
        "created",
    )
    search_fields = (
        "order_number",
        "user__username",
        "user__email",
        "payment__transaction_code",
        "payment__reference_code",
    )
    ordering = ("-created",)
    list_select_related = ("user", "shipping_method", "payment")
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
                    "stock_reduced",
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
    actions = (\n        move_paid_orders_to_processing,\n        move_processing_orders_to_completed,\n        cancel_unpaid_orders,\n    )\n
    @admin.display(description="کاربر", ordering="user__email")
    def user_display(self, obj):
        return obj.user.email or obj.user.username

    @admin.display(description="وضعیت سفارش", ordering="status")
    def get_status_display_value(self, obj):
        return obj.get_status_display()

    @admin.display(description="مبلغ نهایی", ordering="final_amount")
    def get_final_amount_display(self, obj):
        return f"{intcomma(int(obj.final_amount))} تومان"

    @admin.display(description="وضعیت پرداخت", ordering="payment__status")
    def get_payment_status_display(self, obj):
        try:
            return obj.payment.get_status_display()
        except Payment.DoesNotExist:
            return "بدون پرداخت"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
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
    list_display = (
        "order",
        "user",
        "amount_display",
        "status",
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

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderAddressSnapshot)
class OrderAddressSnapshotAdmin(admin.ModelAdmin):
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
    list_editable = ("is_active", "estimated_delivery_days")
    ordering = ("cost",)
    readonly_fields = ("created", "updated")
    fieldsets = (
        ("اطلاعات اصلی", {"fields": ("name", "description", "is_active")}),
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
