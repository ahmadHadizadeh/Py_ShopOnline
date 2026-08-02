from django.urls import path, register_converter
from . import views
from catalog.converters import UnicodeSlugConverter

app_name = "catalog"

register_converter(UnicodeSlugConverter, "uslug")

urlpatterns = [
    path("", views.home, name="product_list"),
    path("category/<uslug:category_slug>/", views.home, name="product_list_by_category"),
    path("product/<uslug:slug>/", views.product_detail, name="product_detail"),
    path("wishlist/toggle/<uslug:slug>/", views.wishlist_toggle_view, name="wishlist_toggle"),
]
