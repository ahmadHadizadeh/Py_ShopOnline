from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from orders.models.shipping import ShippingMethod
from accounts.models.address import Address  # اگر مدل آدرس در پروژه همین‌جاست

User = get_user_model()


class CheckoutPostTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        self.client.login(username="testuser", password="testpass123")

        self.address = Address.objects.create(
            user=self.user,
            is_default=True,
            # این فیلدها را با فیلدهای واقعی مدل آدرس خودت پر کن
        )

        self.shipping_method = ShippingMethod.objects.create(
            is_active=True,
            cost=2000,
            # این فیلدها را با فیلدهای واقعی مدل ارسال خودت پر کن
        )

    def test_checkout_post_address_shipping_note(self):
        url = reverse("cart:checkout")

        data = {
            "address_id": self.address.id,
            "shipping_method_id": self.shipping_method.id,
            "customer_note": "لطفاً سریع ارسال شود",
        }

        response = self.client.post(url, data)

        self.assertIn(response.status_code, [200, 302])
