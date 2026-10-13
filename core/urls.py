"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from catalog.sitemaps import sitemaps
from core.seo import robots_txt

# from .views import home,product_details

# 🚦 Police Traffic 🚦
urlpatterns = [
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("admin/", admin.site.urls),
    path("", include("catalog.urls", namespace="catalog")),
    path("cart/", include("cart.urls", namespace="cart")),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("orders/", include("orders.urls", namespace="orders")),
]

if settings.DEBUG:  # For Development
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
