from django.contrib import admin

from cart.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ("unit_price_snapshot", "subtotal", "created", "updated")
    autocomplete_fields = ("product",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_key", "status", "total_items", "total_price", "created", "updated")
    list_filter = ("status", "created", "updated")
    search_fields = ("user__username", "user__email", "session_key")
    readonly_fields = ("created", "updated", "total_items", "total_price")
    inlines = (CartItemInline,)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "product", "quantity", "unit_price_snapshot", "subtotal", "created")
    list_filter = ("created", "updated")
    search_fields = ("product__name", "product__slug", "cart__session_key", "cart__user__username")
    autocomplete_fields = ("cart", "product")
    readonly_fields = ("subtotal", "created", "updated")
