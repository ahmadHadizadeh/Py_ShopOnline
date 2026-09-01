from django.urls import path
from django.contrib.auth.views import LogoutView
from django.contrib.auth import views as auth_views
from . import otp_views, views

app_name = "accounts"

urlpatterns = [
    # OTP Authentication URLs
    path("ajax/send-otp/", otp_views.send_otp_view, name="send_otp"),
    path("ajax/verify-otp/", otp_views.verify_otp_view, name="verify_otp"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    # Dashboard & Addresses
    path("dashboard/", views.DashboardOrderListView.as_view(), name="dashboard"),
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
    path("dashboard/addresses/", views.AddressListView.as_view(), name="address_list"),
    path(
        "dashboard/addresses/create/",
        views.AddressCreateView.as_view(),
        name="address_create",
    ),
    path(
        "dashboard/addresses/<int:pk>/update/",
        views.AddressUpdateView.as_view(),
        name="address_update",
    ),
    path(
        "dashboard/addresses/<int:pk>/delete/",
        views.AddressDeleteView.as_view(),
        name="address_delete",
    ),
    path(
        "ajax/cities/",
        views.CitiesByProvinceView.as_view(),
        name="cities_by_province",
    ),
]
