from django.core.exceptions import ValidationError
from django.db import models, router, transaction

from .product import Product


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants", verbose_name="محصول"
    )
    name = models.CharField(max_length=100, verbose_name="نام ویژگی")
    value = models.CharField(max_length=100, verbose_name="مقدار")
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="قیمت نهایی (تومان)",
        help_text="قیمت مستقل این تنوع؛ قیمت محصول به آن اضافه نمی‌شود.",
    )
    price_adjustment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
        verbose_name="تغییر قیمت (سازگاری نسخه قبلی)",
    )

    def clean(self):
        super().clean()

        errors = {}

        if self.price is not None and self.price < 0:
            errors["price"] = "قیمت نهایی تنوع نمی‌تواند منفی باشد."

        if self.pk and self.product_id:
            persisted_product_id = (
                type(self).objects.filter(pk=self.pk)
                .values_list("product_id", flat=True)
                .first()
            )
            if (
                persisted_product_id is not None
                and persisted_product_id != self.product_id
                and self.cart_items.exists()
            ):
                errors["product"] = (
                    "واریانتی که در سبد خرید استفاده شده است، "
                    "قابل انتقال به محصول دیگری نیست."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        fields_to_save = (
            None if update_fields is None else set(update_fields)
        )
        must_sync_legacy_value = (
            fields_to_save is None
            or bool(
                fields_to_save
                & {"price", "product", "product_id", "price_adjustment"}
            )
        )

        if not must_sync_legacy_value:
            return super().save(*args, **kwargs)

        if self.price is None:
            raise ValidationError({"price": "قیمت نهایی تنوع الزامی است."})

        using = kwargs.get("using") or router.db_for_write(
            type(self), instance=self
        )

        with transaction.atomic(using=using):
            product_price = (
                Product.objects.using(using)
                .select_for_update()
                .values_list("price", flat=True)
                .get(pk=self.product_id)
            )

            # Keep the legacy formula synchronized for code rollback.
            self.price_adjustment = self.price - product_price

            if fields_to_save is not None:
                kwargs["update_fields"] = (
                    fields_to_save | {"price_adjustment"}
                )

            return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "تنوع محصول"
        verbose_name_plural = "تنوع‌های محصول"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="variant_final_price_gte_0",
            ),
        ]
