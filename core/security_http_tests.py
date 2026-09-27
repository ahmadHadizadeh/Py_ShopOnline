import hashlib
import hmac
from json import dumps
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import SESSION_KEY
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile
from orders.models import Order, Payment


User = get_user_model()


# ---------------------------------------------------------------------------
# 3.7.2 HTTP security contract
# ---------------------------------------------------------------------------


@override_settings(
    ALLOWED_HOSTS=["testserver", "shop.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://shop.example.com"],
)
@pytest.mark.django_db
def test_allowed_hosts_accepts_configured_host_and_rejects_unknown_host():
    client = Client()

    allowed = client.get(
        reverse("accounts:login"),
        HTTP_HOST="shop.example.com",
    )
    rejected = client.get(
        reverse("accounts:login"),
        HTTP_HOST="evil.example.com",
    )

    assert allowed.status_code == 200
    assert rejected.status_code == 400


@override_settings(
    ALLOWED_HOSTS=["testserver", "shop.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://shop.example.com"],
)
@pytest.mark.django_db
def test_csrf_trusted_origin_is_accepted_for_state_changing_logout():
    user = User.objects.create_user(
        username="csrf-origin-user",
        password="testpass123",
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    page = client.get(
        reverse("accounts:dashboard_orders"),
        HTTP_HOST="shop.example.com",
    )
    assert page.status_code == 200

    csrf_token = client.cookies["csrftoken"].value
    response = client.post(
        reverse("accounts:logout"),
        HTTP_HOST="shop.example.com",
        HTTP_ORIGIN="https://shop.example.com",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 302
    assert response.url == "/"


@override_settings(
    ALLOWED_HOSTS=["testserver", "shop.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://shop.example.com"],
)
@pytest.mark.django_db
def test_untrusted_csrf_origin_is_rejected_for_state_changing_logout():
    user = User.objects.create_user(
        username="csrf-reject-user",
        password="testpass123",
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    client.get(
        reverse("accounts:dashboard_orders"),
        HTTP_HOST="shop.example.com",
    )
    csrf_token = client.cookies["csrftoken"].value

    response = client.post(
        reverse("accounts:logout"),
        HTTP_HOST="shop.example.com",
        HTTP_ORIGIN="https://evil.example.com",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 403
    assert client.session.get(SESSION_KEY) is not None


@pytest.mark.django_db
def test_real_otp_login_sets_expected_session_cookie_security():
    client = Client()

    with patch(
        "accounts.otp_views.SMSIRService.verify_otp",
        return_value=(True, "ok"),
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps(
                {
                    "phone": "09123334444",
                    "code": "123456",
                    "next": "/",
                }
            ),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/"
    assert "sessionid" in response.cookies

    session_cookie = response.cookies["sessionid"]

    assert session_cookie["httponly"] == "True"
    assert session_cookie["samesite"] == "Lax"


@pytest.mark.django_db
def test_otp_login_rotates_session_and_authenticates_user():
    client = Client()
    session = client.session
    session["pre_login_marker"] = "guest"
    session.save()
    old_session_key = session.session_key

    with patch(
        "accounts.otp_views.SMSIRService.verify_otp",
        return_value=(True, "ok"),
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps(
                {
                    "phone": "09123334444",
                    "code": "123456",
                    "next": "/cart/checkout/",
                }
            ),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/cart/checkout/"
    assert client.session.get(SESSION_KEY) is not None
    assert client.session.session_key != old_session_key


@pytest.mark.django_db
def test_otp_login_rejects_cross_site_redirect():
    client = Client()

    with patch(
        "accounts.otp_views.SMSIRService.verify_otp",
        return_value=(True, "ok"),
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps(
                {
                    "phone": "09125556666",
                    "code": "123456",
                    "next": "https://evil.example/phishing",
                }
            ),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/"


@pytest.mark.django_db
def test_logout_requires_post_and_ends_authenticated_session():
    user = User.objects.create_user(
        username="http-logout-user",
        password="testpass123",
    )
    client = Client()
    client.force_login(user)

    get_response = client.get(reverse("accounts:logout"))
    assert get_response.status_code == 405

    post_response = client.post(reverse("accounts:logout"))
    assert post_response.status_code == 302
    assert post_response.url == "/"
    assert client.session.get(SESSION_KEY) is None


class PaymentCallbackHttpContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="callback-http-user")
        Profile.objects.get_or_create(user=self.user)

        self.order = Order.objects.create(
            user=self.user,
            status=Order.Status.PENDING,
            final_amount=50000000,
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user,
            amount=50000000,
            status=Payment.Status.PENDING,
            gateway_name="mock_gateway",
            transaction_code="HTTP-CALLBACK-001",
        )
        self.callback_url = reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": "mock_gateway"},
        )

    def callback_signature(self):
        payload = f"{self.payment.transaction_code}:{self.payment.amount}".encode(
            "utf-8"
        )
        return hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

    def test_valid_get_callback_is_not_blocked_by_csrf(self):
        response = self.client.get(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": self.callback_signature(),
            },
        )

        assert response.status_code == 302
        assert response.url == reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )

    @override_settings(CSRF_COOKIE_HTTPONLY=False)
    def test_post_callback_requires_csrf_when_csrf_checks_are_enforced(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": self.callback_signature(),
            },
        )

        assert response.status_code == 403
