from django.contrib import admin
from .models.address import Address

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        'recipient_name', 
        'user_email', 
        'city', 
        'is_default', 
        'phone_number'
    )
    list_filter = ('is_default', 'province', 'created')
    search_fields = ('recipient_name', 'phone_number', 'postal_code', 'user__email')
    list_editable = ('is_default',)
    readonly_fields = ('created', 'updated')
    
    # بهینه‌سازی برای جلوگیری از کوئری‌های اضافی
    list_select_related = ('user',)

    def user_email(self, obj):
        return obj.user.email
    
    user_email.short_description = 'ایمیل کاربر'
    user_email.admin_order_field = 'user__email'

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


