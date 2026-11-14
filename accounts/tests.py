import pytest
from json import dumps

from django.apps import apps
from django.contrib import admin
from unittest.mock import patch
from decimal import Decimal

import jdatetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import NoReverseMatch, reverse

from accounts.models import Address, Profile
from accounts.forms import AddressForm
from accounts.apps import STORE_MANAGER_GROUP_NAME
from accounts.signals import ensure_store_manager_group
from cart.models import Cart, CartItem
from orders.models import Order, OrderAddressSnapshot, OrderItem, ShippingMethod
from catalog.models.category import Category
from catalog.models.product import Product


@pytest.mark.parametrize("phone_number", ["0912345678", "09123456789"])
def test_address_model_accepts_form_phone_number_lengths(
    phone_number, django_user_model
):
    user = django_user_model.objects.create_user(
        username=f"address-valid-{phone_number}"
    )
    address = Address(
        user=user,
        recipient_name="Test recipient",
        phone_number=phone_number,
        postal_code="1234567890",
        province="تهران",
        city="تهران",
        address_line="Test address",
    )

    address.full_clean()


@pytest.mark.parametrize("phone_number", ["091234567", "091234567890"])
def test_address_model_rejects_phone_number_lengths_not_supported_by_form(
    phone_number, django_user_model
):
    user = django_user_model.objects.create_user(
        username=f"address-invalid-{phone_number}"
    )
    address = Address(
        user=user,
        recipient_name="Test recipient",
        phone_number=phone_number,
        postal_code="1234567890",
        province="تهران",
        city="تهران",
        address_line="Test address",
    )

    with pytest.raises(ValidationError, match="شماره تماس"):
        address.full_clean()


@pytest.mark.parametrize(
    ("phone_number", "is_valid"),
    [
        ("0912345678", True),
        ("09123456789", True),
        ("091234567", False),
        ("091234567890", False),
    ],
)
def test_address_form_and_model_share_phone_number_length_contract(
    phone_number, is_valid
):
    form = AddressForm(
        data={
            "recipient_name": "Test recipient",
            "phone_number": phone_number,
            "postal_code": "1234567890",
            "province": "تهران",
            "city": "تهران",
            "address_line": "Test address",
            "is_default": "",
        }
    )

    assert form.is_valid() is is_valid


@pytest.mark.parametrize(
    ("field_name", "expected_message"),
    [
        ("recipient_name", "نام گیرنده را وارد کنید."),
        ("phone_number", "شماره تماس را وارد کنید."),
        ("postal_code", "کد پستی را وارد کنید."),
        ("province", "استان را انتخاب کنید."),
        ("city", "شهر را انتخاب کنید."),
        ("address_line", "نشانی کامل را وارد کنید."),
    ],
)
def test_address_form_required_errors_are_short_and_persian(
    field_name, expected_message
):
    data = {
        "recipient_name": "گیرنده تست",
        "phone_number": "09123456789",
        "postal_code": "1234567890",
        "province": "تهران",
        "city": "تهران",
        "address_line": "نشانی تست",
        "is_default": "",
    }
    data[field_name] = ""

    form = AddressForm(data=data)

    assert not form.is_valid()
    assert expected_message in form.errors[field_name]
    assert all(
        "This field is required" not in error
        for errors in form.errors.values()
        for error in errors
    )


@pytest.mark.django_db
def test_address_form_rejects_city_not_belonging_to_selected_province():
    form = AddressForm(
        data={
            "recipient_name": "گیرنده تست",
            "phone_number": "09123456789",
            "postal_code": "1234567890",
            "province": "تهران",
            "city": "مشهد",
            "address_line": "نشانی تست",
            "is_default": "",
        }
    )

    assert not form.is_valid()
    assert "شهر انتخاب‌شده برای این استان معتبر نیست." in form.errors["city"]


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
def test_address_partial_update_does_not_demote_another_default(django_user_model):
    user = django_user_model.objects.create_user(
        username="address-stale-update-user",
        password="testpass123",
    )
    original = Address.objects.create(
        user=user,
        recipient_name="Original",
        phone_number="09123456789",
        postal_code="1234567890",
        province="تهران",
        city="تهران",
        address_line="آدرس اول",
        is_default=True,
    )
    stale_instance = Address.objects.get(pk=original.pk)

    Address.objects.filter(pk=original.pk).update(is_default=False)
    current_default = Address.objects.create(
        user=user,
        recipient_name="Current default",
        phone_number="09123456780",
        postal_code="1234567891",
        province="تهران",
        city="تهران",
        address_line="آدرس دوم",
        is_default=True,
    )

    stale_instance.recipient_name = "Updated without changing default"
    stale_instance.save(update_fields=["recipient_name"])

    current_default.refresh_from_db()
    assert current_default.is_default is True


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

    assert Profile.objects.get(user=user).is_verified is True
    assert not Cart.objects.filter(pk=guest_cart.pk).exists()
    assert user_cart.items.get(product=product).quantity == 2
    assert user_cart.items.get(product=product).status == CartItem.STATUS_ACTIVE
    assert user_cart.session_key is None

    current_session = client.session
    assert current_session["cart_id"] == user_cart.pk
    assert current_session.session_key != guest_session_key


@pytest.mark.django_db
def test_phone_shaped_username_does_not_imply_verified_mobile(django_user_model):
    user = django_user_model.objects.create_user(
        username="09123456789",
        password="testpass123",
    )
    profile = Profile.objects.get(user=user)
    assert profile.phone_number == "09123456789"
    assert profile.is_verified is False


@pytest.mark.django_db
def test_verify_otp_rejects_inactive_user_without_login_or_cart_merge(
    client, django_user_model
):
    inactive_user = django_user_model.objects.create_user(
        username="09123456789",
        password="testpass123",
        is_active=False,
    )
    profile, _ = Profile.objects.get_or_create(user=inactive_user)
    profile.phone_number = "09123456789"
    profile.save(update_fields=["phone_number"])

    with (
        patch(
            "accounts.otp_views.SMSIRService.verify_otp",
            return_value=(True, "ok"),
        ),
        patch("accounts.otp_views.merge_guest_cart_to_user") as merge_cart,
    ):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps({"phone": "09123456789", "code": "123456"}),
            content_type="application/json",
        )

    assert response.status_code == 403
    assert response.json()["status"] == "error"
    assert client.session.get("_auth_user_id") is None
    assert Profile.objects.get(user=inactive_user).is_verified is False
    merge_cart.assert_not_called()


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


# ---------------------------------------------------------------------------
# OTP / SMS.ir production contracts
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_otp_production_uses_smsir_verify_api():
    from django.test import override_settings
    from accounts.services.otp import SMSIRService

    class FakeResponse:
        status_code = 200
        text = "ok"

        def json(self):
            return {"status": 1, "message": "ok"}

    with override_settings(
        DEBUG=False,
        SMSIR_CONFIG={
            "API_KEY": "TEST_API_KEY",
            "TEMPLATE_ID": 358253,
        },
    ):
        with patch(
            "accounts.services.otp.requests.post",
            return_value=FakeResponse(),
        ) as mocked_post:
            ok, _ = SMSIRService.send_verification_code(
                "09123456789",
                "123456",
            )

    assert ok is True
    mocked_post.assert_called_once()
    call = mocked_post.call_args
    assert call.args[0] == "https://api.sms.ir/v1/send/verify"
    assert call.kwargs["json"]["templateId"] == 358253
    assert call.kwargs["json"]["parameters"] == [
        {"name": "CODE", "value": "123456"}
    ]
    assert call.kwargs["headers"]["x-api-key"] == "TEST_API_KEY"


@pytest.mark.django_db
def test_otp_development_uses_console_instead_of_smsir():
    from django.test import override_settings
    from accounts.services.otp import SMSIRService

    with override_settings(
        DEBUG=True,
        SMSIR_CONFIG={
            "API_KEY": "TEST_API_KEY",
            "TEMPLATE_ID": 358253,
        },
    ):
        with patch("accounts.services.otp.requests.post") as mocked_post:
            ok, message = SMSIRService.send_verification_code(
                "09123456789",
                "123456",
            )

    assert ok is True
    assert "تستی" in message
    mocked_post.assert_not_called()



class StoreIdentityAdminPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username="store-owner-identity-admin",
            email="store-owner-identity@example.test",
            password="OwnerPassword123!",
        )
        self.staff = User.objects.create_user(
            username="store-operator-identity-admin",
            email="store-operator-identity@example.test",
            password="StaffPassword123!",
            is_staff=True,
        )
        self.customer = User.objects.create_user(
            username="store-customer-identity",
            email="store-customer-identity@example.test",
            password="CustomerPassword123!",
        )
        identity_permissions = Permission.objects.filter(
            content_type__app_label="auth",
            content_type__model__in=("user", "group"),
            codename__in=(
                "view_user", "add_user", "change_user", "delete_user",
                "view_group", "add_group", "change_group", "delete_group",
            ),
        )
        self.staff.user_permissions.set(identity_permissions)

    def test_staff_cannot_manage_users_even_with_auth_permissions(self):
        self.client.force_login(self.staff)
        protected_urls = (
            reverse("admin:auth_user_changelist"),
            reverse("admin:auth_user_add"),
            reverse("admin:auth_user_change", args=[self.customer.pk]),
        )
        for url in protected_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

        dashboard = self.client.get(reverse("admin:index"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, "حساب‌های کاربری")
        self.assertNotContains(dashboard, "گروه‌های دسترسی")
        self.assertFalse(admin.site.is_registered(Group))
        with self.assertRaises(NoReverseMatch):
            reverse("admin:auth_group_changelist")

    def test_owner_can_manage_users_without_a_permission_editor(self):
        Group.objects.get_or_create(name="Other permissions group")
        self.client.force_login(self.owner)
        user_list = self.client.get(reverse("admin:auth_user_changelist"))
        user_change = self.client.get(
            reverse("admin:auth_user_change", args=[self.customer.pk])
        )
        self.assertEqual(user_list.status_code, 200)
        self.assertEqual(user_change.status_code, 200)
        self.assertContains(user_list, self.customer.username)
        self.assertContains(user_change, STORE_MANAGER_GROUP_NAME)
        self.assertNotContains(user_change, "Other permissions group")
        self.assertNotContains(user_change, 'id="id_user_permissions"')
        self.assertNotContains(user_change, 'id="id_is_superuser"')
        self.assertFalse(admin.site.is_registered(Group))
        with self.assertRaises(NoReverseMatch):
            reverse("admin:auth_group_changelist")

    def test_owner_add_form_has_no_raw_permission_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("admin:auth_user_add"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="id_is_superuser"')
        self.assertNotContains(response, 'id="id_user_permissions"')

    def test_user_edit_preserves_existing_non_manager_group_memberships(self):
        legacy_group = Group.objects.create(name="Legacy permissions group")
        manager_group = Group.objects.get(name=STORE_MANAGER_GROUP_NAME)
        self.customer.groups.add(legacy_group)

        class UserEditForm:
            instance = self.customer

            def save_m2m(self):
                self.instance.groups.set([manager_group.pk])

        request = RequestFactory().post(
            reverse("admin:auth_user_change", args=[self.customer.pk])
        )
        user_admin = admin.site._registry[get_user_model()]
        user_admin.save_related(request, UserEditForm(), [], change=True)

        self.assertSetEqual(
            set(self.customer.groups.values_list("pk", flat=True)),
            {legacy_group.pk, manager_group.pk},
        )


class StoreManagerGroupProvisioningTests(TestCase):
    expected_permissions = {
        ("catalog", "add_product"), ("catalog", "change_product"),
        ("catalog", "add_category"), ("catalog", "change_category"),
        ("catalog", "add_productvariant"), ("catalog", "change_productvariant"),
        ("catalog", "add_productimage"), ("catalog", "change_productimage"),
        ("catalog", "delete_productimage"),
        ("catalog", "add_productspecification"),
        ("catalog", "change_productspecification"),
        ("catalog", "delete_productspecification"),
        ("catalog", "change_review"),
        ("catalog", "view_inventorymovement"),
        ("orders", "change_order"),
        ("orders", "view_payment"),
        ("orders", "view_orderitem"),
        ("orders", "view_orderaddresssnapshot"),
    }

    def setUp(self):
        self.group = Group.objects.get(name=STORE_MANAGER_GROUP_NAME)

    def test_group_contains_exactly_the_source_controlled_permissions(self):
        actual = set(self.group.permissions.values_list(
            "content_type__app_label", "codename"
        ))
        self.assertSetEqual(actual, self.expected_permissions)

    def test_permission_drift_fails_closed_without_rewriting_group(self):
        unexpected = Permission.objects.get(
            content_type__app_label="auth", codename="view_user"
        )
        self.group.permissions.add(unexpected)
        with self.assertRaisesMessage(RuntimeError, "مطابقت ندارد"):
            ensure_store_manager_group(
                sender=apps.get_app_config("orders"), using="default"
            )
        self.assertTrue(self.group.permissions.filter(pk=unexpected.pk).exists())


class StoreManagerAdminAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="store-manager-operational",
            email="store-manager-operational@example.test",
            password="ManagerPassword123!",
            is_staff=True,
        )
        self.manager.groups.add(Group.objects.get(name=STORE_MANAGER_GROUP_NAME))
        category = Category.objects.create(
            name="Manager access category", slug="manager-access-category"
        )
        self.product = Product.objects.create(
            name="Manager access product", slug="manager-access-product",
            price=100000, stock=5, is_active=True, category=category,
        )

    def test_manager_can_access_intended_store_operations_only(self):
        self.client.force_login(self.manager)
        allowed_urls = (
            reverse("admin:catalog_product_changelist"),
            reverse("admin:catalog_product_add"),
            reverse("admin:catalog_category_changelist"),
            reverse("admin:catalog_category_add"),
            reverse("admin:catalog_productvariant_changelist"),
            reverse("admin:catalog_review_changelist"),
            reverse("admin:catalog_inventorymovement_changelist"),
            reverse("admin:orders_order_changelist"),
            reverse("admin:orders_orderitem_changelist"),
            reverse("admin:orders_orderaddresssnapshot_changelist"),
            reverse("admin:orders_payment_changelist"),
            reverse("admin:catalog_product_inventory", args=[self.product.pk]),
        )
        for url in allowed_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        product_change = self.client.get(
            reverse("admin:catalog_product_change", args=[self.product.pk])
        )
        self.assertEqual(product_change.status_code, 200)
        self.assertContains(product_change, "images-TOTAL_FORMS")
        self.assertContains(product_change, "specs-TOTAL_FORMS")

        denied_urls = (
            reverse("admin:orders_shippingmethod_changelist"),
            reverse("admin:cart_cart_changelist"),
            reverse("admin:accounts_address_changelist"),
            reverse("admin:catalog_wishlist_changelist"),
            reverse("admin:auth_user_changelist"),
        )
        for url in denied_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

        for permission in (
            "orders.change_payment", "catalog.delete_product",
            "catalog.delete_category", "catalog.delete_productvariant",
            "catalog.view_product", "catalog.view_category",
            "catalog.view_productvariant", "catalog.view_productimage",
            "catalog.view_productspecification", "catalog.view_review",
            "orders.view_order",
            "orders.delete_order", "orders.add_payment", "cart.view_cart",
            "accounts.view_address", "orders.change_shippingmethod",
            "auth.change_user",
        ):
            with self.subTest(permission=permission):
                self.assertFalse(self.manager.has_perm(permission))




class StoreManagerAssignmentLimitTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            username="manager-limit-owner",
            email="manager-limit-owner@example.test",
            password="OwnerPassword123!",
        )
        self.first_candidate = get_user_model().objects.create_user(
            username="manager-limit-first",
            password="CandidatePassword123!",
        )
        self.second_candidate = get_user_model().objects.create_user(
            username="manager-limit-second",
            password="CandidatePassword123!",
        )
        self.manager_group = Group.objects.get(name=STORE_MANAGER_GROUP_NAME)

    def test_m2m_assignment_rejects_a_second_manager_even_outside_admin(self):
        self.first_candidate.groups.add(self.manager_group)
        with self.assertRaises(ValidationError):
            self.second_candidate.groups.add(self.manager_group)

        self.assertEqual(
            get_user_model().objects.filter(
                groups=self.manager_group,
                is_superuser=False,
            ).count(),
            1,
        )

    def test_superuser_cannot_be_added_to_the_manager_group(self):
        with self.assertRaises(ValidationError):
            self.owner.groups.add(self.manager_group)

    def test_inactive_users_cannot_be_assigned_as_store_manager_through_either_m2m_side(self):
        self.first_candidate.is_active = False
        self.first_candidate.save(update_fields=["is_active"])
        self.second_candidate.is_active = False
        self.second_candidate.save(update_fields=["is_active"])

        with self.assertRaisesMessage(ValidationError, "حساب کاربری باید فعال باشد"):
            self.first_candidate.groups.add(self.manager_group)
        with self.assertRaisesMessage(ValidationError, "حساب کاربری باید فعال باشد"):
            self.manager_group.user_set.add(self.second_candidate)

        self.assertFalse(
            self.first_candidate.groups.filter(pk=self.manager_group.pk).exists()
        )
        self.assertFalse(
            self.second_candidate.groups.filter(pk=self.manager_group.pk).exists()
        )

    def _manager_change_form(self, user, *, assign_role, staff_checked=False):
        user_admin = admin.site._registry[get_user_model()]
        request = RequestFactory().post("/")
        request.user = self.owner
        form_class = user_admin.get_form(request, obj=user)
        data = {
            "username": user.username,
            "password": user.password,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_active": "on" if user.is_active else "",
            "is_staff": "on" if staff_checked else "",
            "groups": [str(self.manager_group.pk)] if assign_role else [],
        }
        return request, user_admin, form_class(data=data, instance=user)

    def test_manager_assignment_sets_is_staff_in_owner_form(self):
        request, user_admin, form = self._manager_change_form(
            self.first_candidate,
            assign_role=True,
        )
        with transaction.atomic():
            self.assertTrue(form.is_valid(), form.errors)
            self.assertTrue(form.cleaned_data["is_staff"])
            obj = form.save(commit=False)
            user_admin.save_model(request, obj, form, change=True)
            user_admin.save_related(request, form, [], change=True)

        self.first_candidate.refresh_from_db()
        self.assertTrue(self.first_candidate.is_staff)
        self.assertTrue(
            self.first_candidate.groups.filter(pk=self.manager_group.pk).exists()
        )

    def test_revoking_manager_role_revokes_is_staff_in_owner_form(self):
        self.first_candidate.groups.add(self.manager_group)
        self.first_candidate.is_staff = True
        self.first_candidate.save(update_fields=["is_staff"])

        request, user_admin, form = self._manager_change_form(
            self.first_candidate,
            assign_role=False,
            staff_checked=True,
        )
        with transaction.atomic():
            self.assertTrue(form.is_valid(), form.errors)
            self.assertFalse(form.cleaned_data["is_staff"])
            obj = form.save(commit=False)
            user_admin.save_model(request, obj, form, change=True)
            user_admin.save_related(request, form, [], change=True)

        self.first_candidate.refresh_from_db()
        self.assertFalse(self.first_candidate.is_staff)
        self.assertFalse(
            self.first_candidate.groups.filter(pk=self.manager_group.pk).exists()
        )

    def test_existing_staff_without_manager_fails_closed_for_manual_audit(self):
        self.second_candidate.is_staff = True
        self.second_candidate.save(update_fields=["is_staff"])
        request, user_admin, form = self._manager_change_form(
            self.second_candidate,
            assign_role=False,
            staff_checked=True,
        )

        with transaction.atomic():
            self.assertFalse(form.is_valid())
            self.assertIn("is_staff", form.errors)



class ProfileLegacyOTPTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="profile-legacy-otp-user",
            password="testpass123",
        )
        self.profile = Profile.objects.get(user=self.user)

    def test_otp_without_creation_timestamp_is_invalid(self):
        self.profile.otp_code = "123456"
        self.profile.otp_created_at = None
        self.profile.save(update_fields=["otp_code", "otp_created_at"])

        self.assertFalse(self.profile.is_otp_valid("123456"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.otp_code, "123456")

    def test_valid_otp_is_consumed_after_first_success(self):
        from django.utils import timezone

        self.profile.otp_code = "123456"
        self.profile.otp_created_at = timezone.now()
        self.profile.save(update_fields=["otp_code", "otp_created_at"])

        self.assertTrue(self.profile.is_otp_valid("123456"))
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.otp_code)
        self.assertIsNone(self.profile.otp_created_at)
        self.assertFalse(self.profile.is_otp_valid("123456"))

# ---------------------------------------------------------------------------
# Atomic OTP concurrency regression tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_concurrent_otp_sends_do_not_send_two_codes_for_one_phone():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.core.cache import cache
    from django.db import close_old_connections
    from django.test import override_settings

    from accounts.services.otp import SMSIRService

    cache_settings = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "otp-atomic-concurrency-tests",
        }
    }

    with override_settings(CACHES=cache_settings):
        cache.clear()
        phone = "09123456789"
        sms_started = Event()
        release_sms = Event()
        sent_codes = []

        def fake_send(mobile, code):
            sent_codes.append((mobile, code))
            sms_started.set()
            if not release_sms.wait(timeout=5):
                raise TimeoutError("Test did not release the mocked SMS request.")
            return True, "ok"

        def send_from_worker():
            close_old_connections()
            try:
                return SMSIRService.send_otp(phone)
            finally:
                close_old_connections()

        with patch.object(
            SMSIRService,
            "send_verification_code",
            side_effect=fake_send,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(send_from_worker)
                assert sms_started.wait(timeout=5)

                second = executor.submit(send_from_worker)
                second_result = second.result(timeout=5)

                release_sms.set()
                first_result = first.result(timeout=5)

        assert first_result[0] is True
        assert second_result[0] is False
        assert len(sent_codes) == 1
        assert sent_codes[0][0] == phone

        cache.clear()


@pytest.mark.django_db(transaction=True)
def test_concurrent_otp_verification_accepts_the_code_only_once():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.core.cache import cache
    from django.db import close_old_connections
    from django.test import override_settings

    from accounts.services.otp import SMSIRService

    cache_settings = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "otp-atomic-verify-tests",
        }
    }

    with override_settings(CACHES=cache_settings):
        cache.clear()
        phone = "09123456789"
        valid_code = "123456"
        code_key = f"{SMSIRService.CACHE_PREFIX_CODE}{phone}"
        cache.set(
            code_key,
            SMSIRService._hash_code(phone, valid_code),
            timeout=SMSIRService.EXPIRATION_SECONDS,
        )

        hash_started = Event()
        release_hash = Event()
        original_hash = SMSIRService._hash_code.__func__

        def blocking_hash(cls, normalized_phone, code):
            if code == valid_code:
                hash_started.set()
                if not release_hash.wait(timeout=5):
                    raise TimeoutError("Test did not release OTP verification.")
            return original_hash(cls, normalized_phone, code)

        def verify_from_worker(started=None):
            if started is not None:
                started.set()
            close_old_connections()
            try:
                return SMSIRService.verify_otp(phone, valid_code)
            finally:
                close_old_connections()

        with patch.object(
            SMSIRService,
            "_hash_code",
            classmethod(blocking_hash),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(verify_from_worker)
                assert hash_started.wait(timeout=5)

                second_started = Event()
                second = executor.submit(verify_from_worker, second_started)
                assert second_started.wait(timeout=5)

                release_hash.set()
                first_result = first.result(timeout=5)
                second_result = second.result(timeout=5)

        assert first_result[0] is True
        assert second_result[0] is False
        assert cache.get(code_key) is None

        cache.clear()


@pytest.mark.django_db
def test_otp_wrong_attempt_counter_enforces_the_configured_limit():
    from django.core.cache import cache
    from django.test import override_settings

    from accounts.services.otp import SMSIRService

    cache_settings = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "otp-atomic-attempt-tests",
        }
    }

    with override_settings(CACHES=cache_settings):
        cache.clear()
        phone = "09123456789"
        code_key = f"{SMSIRService.CACHE_PREFIX_CODE}{phone}"
        attempts_key = f"{SMSIRService.CACHE_PREFIX_ATTEMPTS}{phone}"

        cache.set(
            code_key,
            SMSIRService._hash_code(phone, "123456"),
            timeout=SMSIRService.EXPIRATION_SECONDS,
        )

        for _ in range(SMSIRService.MAX_VERIFY_ATTEMPTS):
            valid, _ = SMSIRService.verify_otp(phone, "000000")
            assert valid is False

        assert cache.get(attempts_key) == SMSIRService.MAX_VERIFY_ATTEMPTS

        valid, message = SMSIRService.verify_otp(phone, "000000")
        assert valid is False
        assert "بیش از حد مجاز" in message
        assert cache.get(attempts_key) == SMSIRService.MAX_VERIFY_ATTEMPTS

        cache.clear()

@pytest.mark.django_db
def test_otp_login_failure_rolls_back_user_profile_cart_session_and_code(client):
    from django.contrib.auth import login as django_login
    from django.core.cache import cache
    from django.core.management import call_command
    from django.db import connection

    from accounts.services.otp import SMSIRService

    phone = "09123456789"
    code_key = f"{SMSIRService.CACHE_PREFIX_CODE}{phone}"
    code_hash = SMSIRService._hash_code(phone, "123456")

    # DatabaseCache is the configured production backend. Ensure its table
    # exists in the isolated test database so OTP deletion participates in
    # the same transaction as the login workflow.
    if "pyshop_cache" not in connection.introspection.table_names():
        call_command("createcachetable", "pyshop_cache", verbosity=0)
    cache.set(code_key, code_hash, timeout=SMSIRService.EXPIRATION_SECONDS)
    session = client.session
    session.save()
    guest_session_key = session.session_key

    category = Category.objects.create(
        name="OTP Rollback Category",
        slug="otp-rollback-category",
    )
    product = Product.objects.create(
        name="OTP Rollback Product",
        slug="otp-rollback-product",
        price=1500,
        stock=4,
        is_active=True,
        category=category,
    )
    guest_cart = Cart.objects.create(
        session_key=guest_session_key,
        user=None,
        status=Cart.STATUS_ACTIVE,
    )
    guest_item = CartItem.objects.create(
        cart=guest_cart,
        product=product,
        quantity=2,
        unit_price_snapshot=1500,
        status=CartItem.STATUS_ACTIVE,
    )

    session = client.session
    session["cart_id"] = guest_cart.pk
    session.save()

    def login_then_fail(request, user):
        django_login(request, user)
        raise RuntimeError("forced failure after session rotation")

    with patch("accounts.otp_views.login", side_effect=login_then_fail):
        response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps({"phone": phone, "code": "123456"}),
            content_type="application/json",
        )

    assert response.status_code == 500
    assert cache.get(code_key) == code_hash
    assert not get_user_model().objects.filter(username=phone).exists()
    assert not Profile.objects.filter(phone_number=phone).exists()

    guest_cart.refresh_from_db()
    guest_item.refresh_from_db()
    assert guest_cart.session_key == guest_session_key
    assert guest_item.cart_id == guest_cart.pk
    assert guest_item.quantity == 2

    restored_session = client.session
    assert restored_session.session_key == guest_session_key
    assert restored_session["cart_id"] == guest_cart.pk
    assert restored_session.get("_auth_user_id") is None

@pytest.mark.django_db(transaction=True)
def test_otp_send_timeout_keeps_reserved_code_verifiable():
    import requests
    from django.core.cache import cache
    from django.core.management import call_command
    from django.db import connection
    from django.test import override_settings

    from accounts.services.otp import SMSIRService

    if "pyshop_cache" not in connection.introspection.table_names():
        call_command("createcachetable", "pyshop_cache", verbosity=0)

    phone = "09123456789"
    code = "223456"
    code_key = f"{SMSIRService.CACHE_PREFIX_CODE}{phone}"
    cooldown_key = f"{SMSIRService.CACHE_PREFIX_COOLDOWN}{phone}"

    with (
        override_settings(
            DEBUG=False,
            SMSIR_CONFIG={"API_KEY": "TEST_API_KEY", "TEMPLATE_ID": 358253},
        ),
        patch("accounts.services.otp.secrets.randbelow", return_value=123456),
        patch(
            "accounts.services.otp.requests.post",
            side_effect=requests.exceptions.ReadTimeout("response timed out"),
        ) as mocked_post,
    ):
        sent, message, ttl = SMSIRService.send_otp(phone)

    assert sent is False
    assert ttl > 0
    assert "وضعیت ارسال پیامک مشخص نشد" in message
    mocked_post.assert_called_once()
    assert cache.get(code_key) == SMSIRService._hash_code(phone, code)
    assert cache.get(cooldown_key) is not None

    valid, _ = SMSIRService.verify_otp(phone, code)
    assert valid is True
    assert cache.get(code_key) is None
    cache.delete(cooldown_key)

