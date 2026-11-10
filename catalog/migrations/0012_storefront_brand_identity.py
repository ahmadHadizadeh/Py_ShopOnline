from django.core.validators import FileExtensionValidator
from django.db import migrations, models

import catalog.models.storefront_settings


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0011_storefront_hero_copy"),
    ]

    operations = [
        migrations.AddField(
            model_name="storefrontsettings",
            name="store_name",
            field=models.CharField(
                default="آنلاین شاپ",
                help_text="این نام به‌صورت یکسان در هدر، فوتر و عنوان مرورگر نمایش داده می‌شود.",
                max_length=100,
                verbose_name="نام فروشگاه",
            ),
        ),
        migrations.AddField(
            model_name="storefrontsettings",
            name="logo",
            field=models.ImageField(
                blank=True,
                help_text="یک تصویر PNG، JPG یا WEBP با حداکثر حجم ۲ مگابایت؛ همین لوگو در هدر، فوتر و آیکون مرورگر استفاده می‌شود.",
                upload_to="storefront/branding/",
                validators=[
                    FileExtensionValidator(
                        allowed_extensions=("png", "jpg", "jpeg", "webp"),
                        message="فرمت لوگو باید PNG، JPG یا WEBP باشد.",
                    ),
                    catalog.models.storefront_settings.validate_storefront_logo_size,
                ],
                verbose_name="لوگوی فروشگاه",
            ),
        ),
    ]
