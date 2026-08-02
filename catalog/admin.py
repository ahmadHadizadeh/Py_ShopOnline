from django.contrib import admin
from .models.category import Category
from .models.product import Product
from .models.review import Review

from .models.product_media import ProductImage, ProductSpecification

# فیلتر سفارشی برای وضعیت موجودی
class AvailabilityFilter(admin.SimpleListFilter):
    title = 'وضعیت موجودی'
    parameter_name = 'availability'

    def lookups(self, request, model_admin):
        return [('available', 'موجود'), ('unavailable', 'ناموجود')]

    def queryset(self, request, queryset):
        if self.value() == 'available': return queryset.filter(stock__gt=0)
        if self.value() == 'unavailable': return queryset.filter(stock=0)
        return queryset

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1

class ProductSpecificationInline(admin.TabularInline):
    model = ProductSpecification
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    # 'is_available' را از لیست‌ها حذف کردیم چون متد است
    list_display = ['name', 'category', 'price', 'stock', 'status_icon', 'updated']
    list_editable = ['price', 'stock']
    # استفاده از فیلتر سفارشی به جای فیلد ناموجود
    list_filter = ['category', 'brand', AvailabilityFilter]
    search_fields = ['name', 'name_en']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, ProductSpecificationInline]
    ordering = ['-updated']

    # متد برای نمایش آیکون در لیست
    @admin.display(description='موجودی', boolean=True)
    def status_icon(self, obj):
        return obj.is_available

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['product', 'user', 'rating', 'is_approved', 'created']
    list_filter = ['is_approved', 'created']
    list_editable = ['is_approved']
    search_fields = ['product__name', 'user__username', 'comment']
    ordering = ['-created']
