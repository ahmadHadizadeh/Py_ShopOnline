from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models


_https_url_validator = URLValidator(
    schemes=["https"],
    message="نشانی باید یک پیوند معتبر با پروتکل HTTPS باشد.",
)


class StorefrontSettings(models.Model):
    """Single, owner-managed set of optional public storefront links."""

    SINGLETON_PK = 1

    instagram_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[_https_url_validator],
        verbose_name="پیوند اینستاگرام",
        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
    )
    whatsapp_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[_https_url_validator],
        verbose_name="پیوند واتساپ",
        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
    )
    telegram_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[_https_url_validator],
        verbose_name="پیوند تلگرام",
        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
    )
    bale_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[_https_url_validator],
        verbose_name="پیوند بله",
        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
    )

    class Meta:
        verbose_name = "تنظیمات ویترین فروشگاه"
        verbose_name_plural = "تنظیمات ویترین فروشگاه"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=SINGLETON_PK),
                name="storefront_settings_singleton_id_1",
            ),
        ]

    def __str__(self):
        return self._meta.verbose_name

    def save(self, *args, **kwargs):
        if self.pk not in (None, self.SINGLETON_PK):
            raise ValidationError(
                {"id": "تنظیمات ویترین فقط یک رکورد با شناسه ثابت دارد."}
            )
        self.pk = self.SINGLETON_PK
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("رکورد اصلی تنظیمات ویترین قابل حذف نیست.")
