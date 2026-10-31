from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma

from cart.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = (
        "product",
        "variant",
        "unit_price_snapshot",
        "subtotal_display",
        "created",
        "updated",
    )
    autocomplete_fields = ("product", "variant")

    @admin.display(description="جمع ردیف (تومان)")
    def subtotal_display(self, obj):
        if obj.unit_price_snapshot is None or not obj.quantity:
            return "—"
        return f"{intcomma(int(obj.subtotal))} تومان"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return self.readonly_fields + ("quantity", "status")
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_display",
        "status",
        "total_items_display",
        "total_price_display",
        "updated",
    )
    list_filter = ("status", "created", "updated")
    search_fields = ("user__username", "user__email", "session_key")
    list_select_related = ("user",)
    list_per_page = 25
    date_hierarchy = "updated"
    readonly_fields = (
        "user",
        "session_key",
        "status",
        "created",
        "updated",
        "total_items",
        "total_price",
    )
    inlines = (CartItemInline,)

    def has_add_permission(self, request):
        # Carts are created and transitioned by cart/checkout services.
        return False

    @admin.display(description="کاربر", ordering="user__username")
    def user_display(self, obj):
        if not obj.user_id:
            return "مهمان"
        return obj.user.get_full_name() or obj.user.email or obj.user.get_username()

    @admin.display(description="تعداد کالا")
    def total_items_display(self, obj):
        return intcomma(obj.total_items)

    @admin.display(description="جمع اقلام فعال (تومان)")
    def total_price_display(self, obj):
        return f"{intcomma(int(obj.total_price))} تومان"

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == Cart.STATUS_ORDERED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = (
        "cart",
        "product",
        "variant",
        "quantity",
        "unit_price_display",
        "subtotal_display",
        "status",
        "created",
    )
    list_filter = ("status", "created", "updated")
    search_fields = (
        "product__name",
        "variant__name",
        "variant__value",
        "product__slug",
        "cart__session_key",
        "cart__user__username",
        "cart__user__email",
    )
    list_select_related = ("cart", "cart__user", "product", "variant")
    list_per_page = 25
    date_hierarchy = "created"
    autocomplete_fields = ("cart", "product", "variant")
    readonly_fields = (
        "cart",
        "product",
        "variant",
        "unit_price_snapshot",
        "subtotal_display",
        "created",
        "updated",
    )

    @admin.display(description="قیمت هر عدد (تومان)", ordering="unit_price_snapshot")
    def unit_price_display(self, obj):
        return f"{intcomma(int(obj.unit_price_snapshot))} تومان"

    @admin.display(description="جمع ردیف (تومان)")
    def subtotal_display(self, obj):
        if obj.unit_price_snapshot is None or not obj.quantity:
            return "—"
        return f"{intcomma(int(obj.subtotal))} تومان"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.cart_id and obj.cart.status == Cart.STATUS_ORDERED:
            return self.readonly_fields + ("quantity", "status")
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        # Creating cart items outside the pricing/stock service can bypass its rules.
        return False

    def has_delete_permission(self, request, obj=None):
        if obj and obj.cart_id and obj.cart.status == Cart.STATUS_ORDERED:
            return False
        return super().has_delete_permission(request, obj)
