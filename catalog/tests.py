# فایل: catalog/tests.py
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from catalog.models.product import Product
from catalog.models.category import Category
from catalog.models.review import Review
from catalog.services import save_product_review, toggle_wishlist

User = get_user_model()

class BaseTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.category = Category.objects.create(name="Cat", slug="cat")
        self.product1 = Product.objects.create(name="P1", slug="p1", is_active=True, category=self.category)
        self.client = Client()

class ReviewServiceTests(BaseTestCase):
    def test_save_review_unauthenticated_user(self):
        with self.assertRaisesMessage(ValueError, "برای ثبت نظر ابتدا وارد حساب کاربری شوید."):
            save_product_review(None, self.product1, 5, "test")

class WishlistServiceTests(BaseTestCase):
    def test_toggle_wishlist_unauthenticated_user(self):
        with self.assertRaisesMessage(ValueError, "کاربر احراز هویت نشده است."):
            toggle_wishlist(None, self.product1.pk)

class ProductDetailViewTests(BaseTestCase):
    def test_product_detail_post_review_unauthenticated(self):
        url = reverse("catalog:product_detail", kwargs={"slug": self.product1.slug})
        response = self.client.post(url, {"rating": 5, "comment": "test"})
        # در اینجا چون 'login' ندارید، انتظار داریم خطای 403 یا 401 بدهد یا به صفحه اصلی برود
        # تست فقط صحت عدم ثبت نظر را چک می‌کند
        self.assertEqual(Review.objects.count(), 0)


