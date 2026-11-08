from django.core.exceptions import ValidationError
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

    def clean(self):
        super().clean()

        if not self.product_id or self.price_adjustment is None:
            return

        persisted = None
        if self.pk:
            persisted = (
                type(self).objects.filter(pk=self.pk)
                .values("product_id", "price_adjustment")
                .first()
            )

        product_changed = (
            persisted is None or persisted["product_id"] != self.product_id
        )
        adjustment_changed = (
            persisted is None
            or persisted["price_adjustment"] != self.price_adjustment
        )

        errors = {}
        if persisted and product_changed and self.cart_items.exists():
            errors["product"] = (
                "واریانتی که در سبد خرید استفاده شده است، قابل انتقال به محصول دیگری نیست."
            )

        if product_changed or adjustment_changed:
            product_price = Product.objects.filter(pk=self.product_id).values_list(
                "price", flat=True
            ).first()
            if product_price is not None and product_price + self.price_adjustment < 0:
                errors["price_adjustment"] = (
                    "قیمت نهایی واریانت (قیمت محصول به‌علاوه تغییر قیمت) "
                    "نمی‌تواند منفی باشد."
                )

        if errors:
            raise ValidationError(errors)

    class Meta:
        verbose_name = "تنوع محصول"
        verbose_name_plural = "تنوع‌های محصول"
