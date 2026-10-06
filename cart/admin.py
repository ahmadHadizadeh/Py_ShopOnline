from django.contrib import admin

from cart.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ("unit_price_snapshot", "subtotal", "created", "updated")
    autocomplete_fields = ("product", "variant")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return self.readonly_fields + ("product", "variant", "quantity", "status")
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_key", "status", "total_items", "total_price", "created", "updated")
    list_filter = ("status", "created", "updated")
    search_fields = ("user__username", "user__email", "session_key")
    readonly_fields = ("created", "updated", "total_items", "total_price")
    inlines = (CartItemInline,)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return self.readonly_fields + ("user", "session_key", "status")
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "product", "variant", "quantity", "unit_price_snapshot", "subtotal", "created")
    list_filter = ("created", "updated")
    search_fields = ("product__name", "variant__name", "variant__value", "product__slug", "cart__session_key", "cart__user__username")
    autocomplete_fields = ("cart", "product", "variant")
    readonly_fields = ("subtotal", "created", "updated")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.cart_id and obj.cart.status == Cart.STATUS_ORDERED:
            return self.readonly_fields + (
                "cart",
                "product",
                "variant",
                "quantity",
                "unit_price_snapshot",
                "status",
            )
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.cart_id and obj.cart.status == Cart.STATUS_ORDERED:
            return False
        return super().has_delete_permission(request, obj)
