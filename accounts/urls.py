from django.urls import path

from . import otp_views, views

app_name = "accounts"

urlpatterns = [
    # OTP authentication
    path("ajax/send-otp/", otp_views.send_otp_view, name="send_otp"),
    path("ajax/verify-otp/", otp_views.verify_otp_view, name="verify_otp"),
  
    # Addresses
    path(
        "addresses/",
        views.AddressListView.as_view(),
        name="address_list",
    ),
    path(
        "addresses/new/",
        views.AddressCreateView.as_view(),
        name="address_create",
    ),
    path(
        "addresses/<int:pk>/edit/",
        views.AddressUpdateView.as_view(),
        name="address_update",
    ),
    path(
        "addresses/<int:pk>/delete/",
        views.AddressDeleteView.as_view(),
        name="address_delete",
    ),
    path(
        "ajax/cities/",
        views.CitiesByProvinceView.as_view(),
        name="cities_by_province",
    ),
    # Dashboard orders
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
