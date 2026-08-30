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
    # Payment Flow
    path(
        "initiate-payment/<str:order_number>/",
        ProcessPaymentView.as_view(),
        name="initiate_payment",
    ),
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
    # Order Confirmation
    path(
        "order/confirm/<str:order_number>/",
        order_confirmation_view,
        name="order_confirmation",
    ),
]
