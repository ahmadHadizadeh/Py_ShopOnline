# orders/urls.py
from django.urls import path
from orders.views.confirmation import order_confirmation_view
from orders.views.payment import (
    PaymentCallbackView,
    PaymentFailedView,
    PaymentSuccessView,
    ProcessPaymentView,
    mock_payment_gateway_view,
)

app_name = "orders"

urlpatterns = [
    path(
        "initiate-payment/<str:order_number>/",
        ProcessPaymentView.as_view(),
        name="initiate_payment",
    ),
    # Explicit gateway-aware callback for multi-gateway integrations.
    path(
        "payment/callback/<str:gateway_name>/",
        PaymentCallbackView.as_view(),
        name="gateway_payment_callback",
    ),
    # Backward-compatible callback route; defaults to the existing mock gateway.
    path(
        "payment/callback/",
        PaymentCallbackView.as_view(),
        name="payment_callback",
    ),
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
        "mock-payment-gateway/",
        mock_payment_gateway_view,
        name="mock_payment_gateway",
    ),
    path(
        "order/confirm/<str:order_number>/",
        order_confirmation_view,
        name="order_confirmation",
    ),
]
