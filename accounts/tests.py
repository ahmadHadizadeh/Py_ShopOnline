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
def test_address_default_logic(client, user_factory):
    # استفاده از factory برای ساخت یوزر
    user = user_factory()
    
    # ساخت آدرس اول و پیش‌فرض
    addr1 = Address.objects.create(user=user, recipient_name='A1', city='Amol', is_default=True)
    
    # ساخت آدرس دوم و پیش‌فرض کردن آن
    addr2 = Address.objects.create(user=user, recipient_name='A2', city='Tehran', is_default=True)
    
    addr1.refresh_from_db()
    
    assert addr2.is_default is True
    assert addr1.is_default is False

@pytest.mark.django_db
def test_address_idor_prevention(client, user_factory):
    # یوزر A آدرس می‌سازد
    user_a = user_factory()
    addr_a = Address.objects.create(user=user_a, recipient_name='A', city='Amol')
    
    # یوزر B لاگین می‌کند
    user_b = user_factory()
    client.force_login(user_b)
    
    # تلاش یوزر B برای دسترسی به آدرس یوزر A
    response = client.get(reverse('accounts:address_update', kwargs={'pk': addr_a.pk}))
    
    # باید ۴۰۴ دریافت کند (چون در get_queryset فیلتر کردیم)
    assert response.status_code == 404


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
