from django.contrib import admin
from .models.address import Address

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


