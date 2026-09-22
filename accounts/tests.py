import pytest
from json import dumps
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Address
from cart.models import Cart, CartItem
from catalog.models.category import Category
from catalog.models.product import Product


@pytest.mark.django_db
def test_address_default_logic(django_user_model):
    user = django_user_model.objects.create_user(
        username="address-default-user",
        password="testpass123",
    )

    addr1 = Address.objects.create(
        user=user,
        recipient_name="A1",
        phone_number="09123456789",
        postal_code="1234567890",
        province="مازندران",
        city="آمل",
        address_line="آدرس تست اول",
        is_default=True,
    )

    addr2 = Address.objects.create(
        user=user,
        recipient_name="A2",
        phone_number="09123456780",
        postal_code="1234567891",
        province="تهران",
        city="تهران",
        address_line="آدرس تست دوم",
        is_default=True,
    )

    addr1.refresh_from_db()

    assert addr2.is_default is True
    assert addr1.is_default is False


@pytest.mark.django_db
def test_address_idor_prevention(client, django_user_model):
    user_a = django_user_model.objects.create_user(
        username="address-owner-a",
        password="testpass123",
    )

    addr_a = Address.objects.create(
        user=user_a,
        recipient_name="A",
        phone_number="09123456789",
        postal_code="1234567890",
        province="مازندران",
        city="آمل",
        address_line="آدرس مالک A",
    )

    user_b = django_user_model.objects.create_user(
        username="address-owner-b",
        password="testpass123",
    )

    client.force_login(user_b)

    response = client.get(
        reverse(
            "accounts:address_update",
            kwargs={"pk": addr_a.pk},
        )
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_login_page_renders_otp_entry(client):
    response = client.get(
        reverse("accounts:login"),
        QUERY_STRING="next=/cart/checkout/",
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'id="otp-modal"' in content
    assert "ورود / ثبت‌نام" in content


# ---------------------------------------------------------------------------
# OTP + guest-cart merge integration
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_verify_otp_merges_guest_cart_before_login_and_preserves_cart_id(client):
    session = client.session
    session.save()
    guest_session_key = session.session_key

    category = Category.objects.create(
        name="OTP Merge Category",
        slug="otp-merge-category",
    )
    product = Product.objects.create(
        name="OTP Merge Product",
        slug="otp-merge-product",
        price=1000,
        stock=5,
        is_active=True,
        category=category,
    )
    guest_cart = Cart.objects.create(
        session_key=guest_session_key,
        user=None,
        status=Cart.STATUS_ACTIVE,
    )
    CartItem.objects.create(
        cart=guest_cart,
        product=product,
        quantity=2,
        unit_price_snapshot=1000,
        status=CartItem.STATUS_ACTIVE,
    )

    with patch(
        "accounts.otp_views.SMSIRService.verify_otp",
        return_value=(True, "ok"),
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps({"phone": "09123456789", "code": "123456"}),
            content_type="application/json",
        )

    assert response.status_code == 200

    User = get_user_model()
    user = User.objects.get(username="09123456789")
    user_cart = Cart.objects.get(user=user, status=Cart.STATUS_ACTIVE)

    assert not Cart.objects.filter(pk=guest_cart.pk).exists()
    assert user_cart.items.get(product=product).quantity == 2
    assert user_cart.items.get(product=product).status == CartItem.STATUS_ACTIVE
    assert user_cart.session_key is None

    current_session = client.session
    assert current_session["cart_id"] == user_cart.pk
    assert current_session.session_key != guest_session_key


@pytest.mark.django_db
def test_verify_otp_returns_safe_internal_redirect(client):
    with patch(
        "accounts.otp_views.SMSIRService.verify_otp",
        return_value=(True, "ok"),
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps(
                {
                    "phone": "09111111111",
                    "code": "123456",
                    "next": "/cart/checkout/",
                }
            ),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/cart/checkout/"


@pytest.mark.django_db
def test_verify_otp_rejects_external_redirect(client):
    with patch(
        "accounts.otp_views.SMSIRService.verify_otp",
        return_value=(True, "ok"),
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps(
                {
                    "phone": "09222222222",
                    "code": "123456",
                    "next": "https://evil.example/phishing",
                }
            ),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/"
