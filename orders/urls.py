# orders/urls.py
from django.urls import path
from .views.confirmation import order_confirmation_view
from .views.payment import (
    ProcessPaymentView,
    PaymentCallbackView,
    PaymentSuccessView,
    PaymentFailedView,
    mock_payment_gateway_view,
    OrderListView,
    OrderDetailView,
)

app_name = "orders"

urlpatterns = [
    path(
        "initiate-payment/<str:order_number>/",
        ProcessPaymentView.as_view(),
        name="initiate_payment",
    ),
    path("payment/callback/", PaymentCallbackView.as_view(), name="payment_callback"),
    path(
        "payment/success/<str:order_number>/",
        PaymentSuccessView.as_view(),
        name="payment_success",
    ),
    path(
        "payment/failed/<str:order_number>/",
        PaymentFailedView.as_view(),
        name="payment_failed",
    ),
    path(
        "mock-payment-gateway/", mock_payment_gateway_view, name="mock_payment_gateway"
    ),
    path("my-orders/", OrderListView.as_view(), name="order_list"),
    path(
        "my-orders/<str:order_number>/", OrderDetailView.as_view(), name="order_detail"
    ),
    path(
        "order/confirm/<str:order_number>/",
        order_confirmation_view,
        name="order_confirmation",
    ),
]
