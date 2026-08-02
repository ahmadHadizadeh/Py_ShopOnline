from django.db import models
from .product import Product


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants", verbose_name="محصول"
    )
    name = models.CharField(max_length=100, verbose_name="نام ویژگی")
    value = models.CharField(max_length=100, verbose_name="مقدار")
    price_adjustment = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="تغییر قیمت"
    )

    class Meta:
        verbose_name = "تنوع محصول"
        verbose_name_plural = "تنوع‌های محصول"
