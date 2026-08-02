from django.urls import path

from cart import views

app_name = "cart"

urlpatterns = [
    path("", views.cart_detail, name="detail"),
    path("add/<int:product_id>/", views.add_to_cart, name="add"),
    path("items/<int:item_id>/update/", views.update_cart_item, name="update_item"),
    path("items/<int:item_id>/remove/", views.remove_cart_item, name="remove_item"),
    path("items/<int:item_id>/save/", views.save_for_later, name="save_for_later"),
    path("items/<int:item_id>/move-to-cart/", views.move_to_cart, name="move_to_cart"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
]
