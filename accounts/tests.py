import pytest
from json import dumps
from unittest.mock import patch
from decimal import Decimal

import jdatetime

from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Address
from cart.models import Cart, CartItem
from orders.models import Order, OrderAddressSnapshot, OrderItem, ShippingMethod
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


@pytest.mark.django_db
def test_dashboard_logout_uses_post_form(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dashboard-logout-user",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("accounts:dashboard_orders"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")

    marker = "خروج از حساب کاربری"
    marker_index = content.index(marker)
    form_start = content.rfind("<form", 0, marker_index)
    form_end = content.find("</form>", marker_index)

    assert form_start >= 0
    assert form_end >= 0

    logout_form = content[form_start : form_end + len("</form>")]
    assert 'method="POST"' in logout_form
    assert 'action="/accounts/logout/"' in logout_form
    assert 'name="csrfmiddlewaretoken"' in logout_form
    assert '<button type="submit"' in logout_form


@pytest.mark.django_db
def test_logout_ends_authenticated_session(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="logout-user",
        password="testpass123",
    )
    client.force_login(user)

    response = client.post(reverse("accounts:logout"))

    assert response.status_code == 302
    assert response.url == "/"
    assert client.session.get("_auth_user_id") is None


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


# ---------------------------------------------------------------------------
# Dashboard order contracts
# ---------------------------------------------------------------------------
def _create_dashboard_order(user, *, status, subtotal=100000, discount=0, shipping=0):
    return Order.objects.create(
        user=user,
        status=status,
        subtotal_amount=Decimal(str(subtotal)),
        discount_amount=Decimal(str(discount)),
        shipping_amount=Decimal(str(shipping)),
    )


@pytest.mark.django_db
def test_dashboard_orders_requires_authentication(client):
    response = client.get(reverse("accounts:dashboard_orders"))

    assert response.status_code == 302
    assert response.url.startswith("/accounts/login/?next=")


@pytest.mark.django_db
def test_dashboard_order_list_isolated_by_owner(client, django_user_model):
    owner = django_user_model.objects.create_user(username="dashboard-owner")
    other_user = django_user_model.objects.create_user(username="dashboard-other")

    owner_order = _create_dashboard_order(
        owner,
        status=Order.Status.PAID,
        subtotal=120000,
    )
    other_order = _create_dashboard_order(
        other_user,
        status=Order.Status.PAID,
        subtotal=220000,
    )

    client.force_login(owner)

    response = client.get(reverse("accounts:dashboard_orders"))

    assert response.status_code == 200
    visible_orders = {order.pk for order in response.context["orders"]}
    assert visible_orders == {owner_order.pk}
    assert str(owner_order.order_number).encode() in response.content
    assert str(other_order.order_number).encode() not in response.content


@pytest.mark.django_db
def test_dashboard_order_detail_rejects_other_user_order(
    client,
    django_user_model,
):
    owner = django_user_model.objects.create_user(username="detail-owner")
    other_user = django_user_model.objects.create_user(username="detail-other")

    order = _create_dashboard_order(
        owner,
        status=Order.Status.PAID,
        subtotal=120000,
    )

    client.force_login(other_user)

    response = client.get(
        reverse(
            "accounts:dashboard_order_detail",
            kwargs={"order_number": order.order_number},
        )
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_dashboard_order_list_status_filters_are_contractual(
    client,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="dashboard-filters")
    pending = _create_dashboard_order(user, status=Order.Status.PENDING)
    paid = _create_dashboard_order(user, status=Order.Status.PAID)
    processing = _create_dashboard_order(user, status=Order.Status.PROCESSING)
    completed = _create_dashboard_order(user, status=Order.Status.COMPLETED)
    cancelled = _create_dashboard_order(user, status=Order.Status.CANCELLED)

    client.force_login(user)

    all_response = client.get(reverse("accounts:dashboard_orders"))
    assert {
        order.pk for order in all_response.context["orders"]
    } == {
        pending.pk,
        paid.pk,
        processing.pk,
        completed.pk,
        cancelled.pk,
    }

    current_response = client.get(
        reverse("accounts:dashboard_orders"),
        {"status": "current"},
    )
    assert {
        order.pk for order in current_response.context["orders"]
    } == {pending.pk, paid.pk, processing.pk}

    delivered_response = client.get(
        reverse("accounts:dashboard_orders"),
        {"status": "delivered"},
    )
    assert {
        order.pk for order in delivered_response.context["orders"]
    } == {completed.pk}

    cancelled_response = client.get(
        reverse("accounts:dashboard_orders"),
        {"status": "canceled"},
    )
    assert {
        order.pk for order in cancelled_response.context["orders"]
    } == {cancelled.pk}

    invalid_response = client.get(
        reverse("accounts:dashboard_orders"),
        {"status": "invalid-status"},
    )
    assert invalid_response.context["selected_status"] == "all"
    assert {
        order.pk for order in invalid_response.context["orders"]
    } == {
        pending.pk,
        paid.pk,
        processing.pk,
        completed.pk,
        cancelled.pk,
    }


@pytest.mark.django_db
def test_dashboard_order_list_pagination_preserves_status_filter(
    client,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="dashboard-pages")

    for _ in range(11):
        _create_dashboard_order(user, status=Order.Status.PAID)

    client.force_login(user)

    response = client.get(
        reverse("accounts:dashboard_orders"),
        {"status": "current", "page": 1},
    )

    assert response.status_code == 200
    assert response.context["paginator"].num_pages == 2
    assert len(response.context["orders"]) == 10
    assert "?page=2&status=current" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_dashboard_order_dates_render_as_jalali_in_list_and_detail(
    client,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="dashboard-date")

    order = _create_dashboard_order(
        user,
        status=Order.Status.PAID,
        subtotal=150000,
    )
    expected_jalali = jdatetime.datetime.fromgregorian(
        datetime=order.created
    ).strftime("%Y/%m/%d - %H:%M")

    client.force_login(user)

    list_response = client.get(reverse("accounts:dashboard_orders"))
    assert expected_jalali in list_response.content.decode("utf-8")

    detail_response = client.get(
        reverse(
            "accounts:dashboard_order_detail",
            kwargs={"order_number": order.order_number},
        )
    )
    assert expected_jalali in detail_response.content.decode("utf-8")


@pytest.mark.django_db
def test_dashboard_order_detail_renders_order_snapshot_contract(
    client,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="dashboard-detail")

    shipping_method = ShippingMethod.objects.create(
        name="ارسال تستی",
        cost=25000,
        estimated_delivery_days=3,
    )
    order = _create_dashboard_order(
        user,
        status=Order.Status.PROCESSING,
        subtotal=250000,
        shipping=25000,
    )
    OrderAddressSnapshot.objects.create(
        order=order,
        recipient_name="گیرنده تست",
        recipient_mobile="09120000000",
        postal_code="1234567890",
        province="تهران",
        city="تهران",
        address_line="خیابان تست، پلاک ۱۰",
    )
    OrderItem.objects.create(
        order=order,
        product_name="محصول تست",
        variant_name="رنگ: آبی",
        quantity=2,
        unit_price=125000,
        subtotal_price=250000,
    )
    order.shipping_method = shipping_method
    order.save(
        update_fields=[
            "shipping_method",
            "updated",
        ]
    )

    client.force_login(user)

    response = client.get(
        reverse(
            "accounts:dashboard_order_detail",
            kwargs={"order_number": order.order_number},
        )
    )

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "محصول تست" in content
    assert "رنگ: آبی" in content
    assert "گیرنده تست" in content
    assert "09120000000" in content
    assert "1234567890" in content
    assert "خیابان تست، پلاک ۱۰" in content
    assert "ارسال تستی" in content
    assert "250,000" in content
    assert "500,000" not in content


@pytest.mark.django_db
def test_dashboard_alias_routes_expose_same_order_list(
    client,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="dashboard-alias")
    _create_dashboard_order(user, status=Order.Status.PAID)

    client.force_login(user)

    dashboard_response = client.get(reverse("accounts:dashboard"))
    orders_response = client.get(reverse("accounts:dashboard_orders"))

    assert dashboard_response.status_code == 200
    assert orders_response.status_code == 200
    assert dashboard_response.context["orders"].count() == 1
    assert orders_response.context["orders"].count() == 1

    order_number = str(dashboard_response.context["orders"][0].order_number)
    assert str(orders_response.context["orders"][0].order_number) == order_number
    assert order_number in dashboard_response.content.decode("utf-8")
    assert order_number in orders_response.content.decode("utf-8")

    assert dashboard_response.wsgi_request.resolver_match.func.view_class is orders_response.wsgi_request.resolver_match.func.view_class
