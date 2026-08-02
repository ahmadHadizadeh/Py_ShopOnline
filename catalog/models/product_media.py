# catalog/models/product_media.py
from django.db import models
from .product import Product

class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="محصول",
    )
    image = models.ImageField(upload_to="gallery/%Y/%m/%d/", verbose_name="تصویر")
    alt_text = models.CharField(max_length=200, blank=True, verbose_name="متن جایگزین")

    class Meta:
        verbose_name = "تصویر محصول"
        verbose_name_plural = "گالری تصاویر محصول"

    def __str__(self):
        return self.alt_text or self.product.name


class ProductSpecification(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="specs",
        verbose_name="محصول",
    )
    title = models.CharField(max_length=100, verbose_name="عنوان ویژگی")
    value = models.CharField(max_length=255, verbose_name="مقدار ویژگی")

    class Meta:
        verbose_name = "مشخصه فنی محصول"
        verbose_name_plural = "مشخصات فنی محصول"

    def __str__(self):
        return f"{self.title}: {self.value}"
