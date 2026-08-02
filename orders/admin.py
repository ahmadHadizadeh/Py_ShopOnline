from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma
from .models.shipping import ShippingMethod


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    # نمایش ستون‌ها در لیست اصلی
    list_display = (
        "name",
        "get_cost_display",
        "get_threshold_display",
        "estimated_delivery_days",
        "is_active",
        "updated",
    )

    # فیلترهای سمت راست
    list_filter = ("is_active", "estimated_delivery_days")

    # فیلدهای قابل جستجو
    search_fields = ("name", "description")

    # ویرایش سریع از داخل لیست (بهینه شده برای سرعت ادمین)
    list_editable = ("is_active", "estimated_delivery_days")

    # ترتیب نمایش
    ordering = ("cost",)

    # فیلدهایی که فقط خواندنی هستند
    readonly_fields = ("created", "updated")

    # فیلدبندی فرم ویرایش (Fieldsets) برای نظم بهتر
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

    # متدهای نمایش شخصی‌سازی شده برای خوانایی مبالغ (اصل ۱۳)
    @admin.display(description="هزینه ارسال (تومان)")
    def get_cost_display(self, obj):
        return f"{intcomma(int(obj.cost))} تومان"

    @admin.display(description="حداقل خرید برای ارسال رایگان")
    def get_threshold_display(self, obj):
        if obj.free_shipping_threshold:
            return f"{intcomma(int(obj.free_shipping_threshold))} تومان"
        return "بدون سقف"
