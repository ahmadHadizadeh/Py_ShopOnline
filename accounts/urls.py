from django.urls import path, include
from . import views

app_name = "accounts"

urlpatterns = [
    # نمایش لیست آدرس‌ها
    path("addresses/", views.AddressListView.as_view(), name="address_list"),
    # ایجاد آدرس جدید
    path("addresses/new/", views.AddressCreateView.as_view(), name="address_create"),
    # ویرایش آدرس (با دریافت pk)
    # استفاده از <int:pk> تضمین می‌کند که فقط اعداد صحیح به عنوان آیدی پذیرفته شوند
    path(
        "addresses/<int:pk>/edit/",
        views.AddressUpdateView.as_view(),
        name="address_update",
    ),
    # حذف آدرس (با دریافت pk)
    path(
        "addresses/<int:pk>/delete/",
        views.AddressDeleteView.as_view(),
        name="address_delete",
    ),
    path(
        "ajax/cities/", views.CitiesByProvinceView.as_view(), name="cities_by_province"
    ),
    path(
        "dashboard/orders/",
        views.DashboardOrderListView.as_view(),
        name="dashboard_orders",
    ),
    path(
        "dashboard/orders/<str:order_number>/",
        views.DashboardOrderDetailView.as_view(),
        name="dashboard_order_detail",
    ),
]
