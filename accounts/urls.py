from django.urls import path, include
from django.contrib.auth import views as auth_views
from . import views
from . import otp_views

app_name = "accounts"

urlpatterns = [
    # Auth URLs
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # 2. این دو مسیر را اضافه کن
    path("ajax/send-otp/", otp_views.send_otp_view, name="send_otp"),
    path("ajax/verify-otp/", otp_views.verify_otp_view, name="verify_otp"),
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
