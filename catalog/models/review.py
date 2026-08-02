from django.db import models
from django.conf import settings
from .product import Product

RATING_CHOICES = [(i, i) for i in range(1, 6)]


class Review(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name="محصول",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="کاربر",
    )
    rating = models.PositiveIntegerField(
        choices=RATING_CHOICES,
        verbose_name="امتیاز",
    )
    comment = models.TextField(verbose_name="نظر")
    is_approved = models.BooleanField(default=False, verbose_name="تأیید شده")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "نظر"
        verbose_name_plural = "نظرات"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "user"],
                name="unique_review_per_product_user",
            )
        ]
