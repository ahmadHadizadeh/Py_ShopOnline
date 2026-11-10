import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0009_category_image"),
    ]

    operations = [
        migrations.CreateModel(
            name="StorefrontSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "instagram_url",
                    models.URLField(
                        blank=True,
                        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
                        max_length=500,
                        validators=[
                            django.core.validators.URLValidator(
                                message="نشانی باید یک پیوند معتبر با پروتکل HTTPS باشد.",
                                schemes=["https"],
                            )
                        ],
                        verbose_name="پیوند اینستاگرام",
                    ),
                ),
                (
                    "whatsapp_url",
                    models.URLField(
                        blank=True,
                        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
                        max_length=500,
                        validators=[
                            django.core.validators.URLValidator(
                                message="نشانی باید یک پیوند معتبر با پروتکل HTTPS باشد.",
                                schemes=["https"],
                            )
                        ],
                        verbose_name="پیوند واتساپ",
                    ),
                ),
                (
                    "telegram_url",
                    models.URLField(
                        blank=True,
                        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
                        max_length=500,
                        validators=[
                            django.core.validators.URLValidator(
                                message="نشانی باید یک پیوند معتبر با پروتکل HTTPS باشد.",
                                schemes=["https"],
                            )
                        ],
                        verbose_name="پیوند تلگرام",
                    ),
                ),
                (
                    "bale_url",
                    models.URLField(
                        blank=True,
                        help_text="نشانی کامل HTTPS؛ در صورت خالی بودن نمایش داده نمی‌شود.",
                        max_length=500,
                        validators=[
                            django.core.validators.URLValidator(
                                message="نشانی باید یک پیوند معتبر با پروتکل HTTPS باشد.",
                                schemes=["https"],
                            )
                        ],
                        verbose_name="پیوند بله",
                    ),
                ),
            ],
            options={
                "verbose_name": "تنظیمات ویترین فروشگاه",
                "verbose_name_plural": "تنظیمات ویترین فروشگاه",
            },
        ),
        migrations.AddConstraint(
            model_name="storefrontsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(("id", 1)),
                name="storefront_settings_singleton_id_1",
            ),
        ),
    ]
