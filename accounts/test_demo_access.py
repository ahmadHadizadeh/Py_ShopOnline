from json import dumps

from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Address, Profile
from core.demo_security import DEMO_CUSTOMER_USERNAME_PREFIX


User = get_user_model()


@override_settings(DEMO_MODE=True)
class DemoCustomerAccessTests(TestCase):
    def test_demo_access_creates_private_customer_and_logs_in(self):
        client = Client()
        response = client.post(
            reverse("accounts:demo_access"),
            {"next": "/cart/checkout/"},
        )

        self.assertRedirects(response, "/cart/checkout/", fetch_redirect_response=False)
        user_id = int(client.session[SESSION_KEY])
        user = User.objects.get(pk=user_id)

        self.assertTrue(user.username.startswith(DEMO_CUSTOMER_USERNAME_PREFIX))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(Profile.objects.filter(user=user).exists())
        demo_address = Address.objects.get(user=user, is_default=True)
        self.assertEqual(demo_address.phone_number, "00000000000")
        self.assertIn("آزمایشی", demo_address.address_line)
        self.assertEqual(
            client.session["demo_customer_username"],
            user.username,
        )
        self.assertEqual(client.session.get_expiry_age(), 24 * 60 * 60)

    def test_demo_access_does_not_replace_authenticated_manager(self):
        manager = User.objects.create_user(
            username="demo-manager-test",
            password="ManagerPassword123!",
            is_staff=True,
        )
        client = Client()
        client.force_login(manager)

        response = client.post(
            reverse("accounts:demo_access"),
            {"next": "/management/"},
        )

        self.assertRedirects(response, "/management/", fetch_redirect_response=False)
        self.assertEqual(int(client.session[SESSION_KEY]), manager.pk)
        self.assertFalse(
            User.objects.filter(
                username__startswith=DEMO_CUSTOMER_USERNAME_PREFIX
            ).exists()
        )

    def test_each_browser_session_gets_a_different_demo_customer(self):
        first_client = Client()
        second_client = Client()

        first_client.post(reverse("accounts:demo_access"))
        second_client.post(reverse("accounts:demo_access"))

        first_user_id = int(first_client.session[SESSION_KEY])
        second_user_id = int(second_client.session[SESSION_KEY])

        self.assertNotEqual(first_user_id, second_user_id)
        self.assertEqual(
            User.objects.filter(
                username__startswith=DEMO_CUSTOMER_USERNAME_PREFIX
            ).count(),
            2,
        )

    def test_external_redirect_is_rejected(self):
        client = Client()
        response = client.post(
            reverse("accounts:demo_access"),
            {"next": "https://attacker.example/phishing"},
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_live_otp_endpoints_are_disabled_in_demo(self):
        client = Client()

        send_response = client.post(
            reverse("accounts:send_otp"),
            data=dumps({"phone": "09123456789"}),
            content_type="application/json",
        )
        verify_response = client.post(
            reverse("accounts:verify_otp"),
            data=dumps(
                {
                    "phone": "09123456789",
                    "code": "123456",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(send_response.status_code, 404)
        self.assertEqual(verify_response.status_code, 404)


@override_settings(DEMO_MODE=False)
class DemoAccessDisabledOutsideDemoTests(TestCase):
    def test_demo_access_route_is_not_available_on_normal_store(self):
        response = self.client.post(reverse("accounts:demo_access"))
        self.assertEqual(response.status_code, 404)
