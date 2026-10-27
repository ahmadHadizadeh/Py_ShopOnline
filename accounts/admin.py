from django.contrib import admin
from django.utils.html import format_html

from .models.address import Address
from .models.profile import Profile

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "recipient_name",
        "user_email",
        "phone_number",
        "province",
        "city",
        "is_default",
        "updated",
    )
    list_filter = ("is_default", "province", "created")
    search_fields = (
        "recipient_name",
        "phone_number",
        "postal_code",
        "province",
        "city",
        "user__email",
        "user__username",
    )
    list_editable = ("is_default",)
    list_per_page = 25
    date_hierarchy = "updated"
    readonly_fields = ("created", "updated")
    list_select_related = ("user",)

    @admin.display(description="کاربر", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email or obj.user.get_username()

    # فیلدست برای دسته‌بندی بهتر در صفحه ویرایش تکی
    fieldsets = (
        ('اطلاعات کاربر و گیرنده', {
            'fields': ('user', 'recipient_name', 'phone_number')
        }),
        ('اطلاعات پستی', {
            'fields': ('province', 'city', 'address_line', 'postal_code')
        }),
        ('وضعیت', {
            'fields': ('is_default', 'created', 'updated')
        }),
    )

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    # OTP values and their timestamps are intentionally excluded from the admin UI.
    list_display = ("user_display", "phone_number", "verification_badge", "created_at")
    list_filter = ("is_verified", "created_at")
    search_fields = ("phone_number", "user__username", "user__email")
    list_select_related = ("user",)
    list_per_page = 25
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    fields = ("user_display", "phone_number", "is_verified", "created_at", "updated_at")
    readonly_fields = fields

    @admin.display(description="مشتری", ordering="user__email")
    def user_display(self, obj):
        return obj.user.get_full_name() or obj.user.email or obj.user.get_username()

    @admin.display(description="وضعیت تأیید", ordering="is_verified")
    def verification_badge(self, obj):
        tone = "success" if obj.is_verified else "warning"
        label = "تأیید شده" if obj.is_verified else "تأیید نشده"
        return format_html(
            '<span class="store-status-pill store-status-pill--{}">{}</span>',
            tone,
            label,
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

