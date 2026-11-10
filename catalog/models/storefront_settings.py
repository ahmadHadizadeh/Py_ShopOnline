from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, URLValidator
from django.db import models


_https_url_validator = URLValidator(
    schemes=["https"],
    message="نشانی باید یک پیوند معتبر با پروتکل HTTPS باشد.",
)


MAX_STOREFRONT_LOGO_SIZE = 2 * 1024 * 1024


def validate_storefront_logo_size(value):
    """Limit public storefront logo uploads to 2 MiB."""
    if value and value.size > MAX_STOREFRONT_LOGO_SIZE:
        raise ValidationError("حجم لوگوی فروشگاه نباید بیشتر از ۲ مگابایت باشد.")


class StorefrontSettings(models.Model):
    """Single, owner-managed set of optional public storefront links."""

    SINGLETON_PK = 1
    DEFAULT_STORE_NAME = "آنلاین شاپ"
    DEFAULT_HERO_TITLE_LINE_1 = "محصول موردنیازتان، فقط چند کلیک با شما فاصله دارد."
    DEFAULT_HERO_TITLE_LINE_2 = "در آنلاین‌شاپ احمد، ساده و آگاهانه انتخاب کنید."

    store_name = models.CharField(
        max_length=100,
        default=DEFAULT_STORE_NAME,
        verbose_name="نام فروشگاه",
        help_text="این نام به‌صورت یکسان در هدر، فوتر و عنوان مرورگر نمایش داده می‌شود.",
    )
    logo = models.ImageField(
        upload_to="storefront/branding/",
        blank=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=("png", "jpg", "jpeg", "webp"),
                message="فرمت لوگو باید PNG، JPG یا WEBP باشد.",
            ),
            validate_storefront_logo_size,
        ],
        verbose_name="لوگوی فروشگاه",
        help_text="یک تصویر PNG، JPG یا WEBP با حداکثر حجم ۲ مگابایت؛ همین لوگو در هدر، فوتر و آیکون مرورگر استفاده می‌شود.",
    )

    hero_title_line_1 = models.CharField(
        max_length=160,
        default=DEFAULT_HERO_TITLE_LINE_1,
        verbose_name="خط اول عنوان بنر صفحه اصلی",
        help_text="متن اصلی بنر صفحه اصلی؛ حداکثر ۱۶۰ نویسه.",
    )
    hero_title_line_2 = models.CharField(
        max_length=160,
        default=DEFAULT_HERO_TITLE_LINE_2,
        verbose_name="خط دوم عنوان بنر صفحه اصلی",
        help_text="متن تأکیدی زیر خط اول؛ حداکثر ۱۶۰ نویسه.",
    )

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
                condition=models.Q(id=1),
                name="storefront_settings_singleton_id_1",
            ),
        ]

    def __str__(self):
        return self._meta.verbose_name

    def clean(self):
        super().clean()
        if self.pk is None:
            self.pk = self.SINGLETON_PK

    def save(self, *args, **kwargs):
        if self.pk not in (None, self.SINGLETON_PK):
            raise ValidationError(
                {"id": "تنظیمات ویترین فقط یک رکورد با شناسه ثابت دارد."}
            )
        self.pk = self.SINGLETON_PK
        if self._state.adding:
            kwargs.setdefault("force_insert", True)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("رکورد اصلی تنظیمات ویترین قابل حذف نیست.")
