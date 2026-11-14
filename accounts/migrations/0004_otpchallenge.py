from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_profile_phone_number"),
    ]

    operations = [
        migrations.CreateModel(
            name="OTPChallenge",
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
                    "phone_hash",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        verbose_name="شناسه هش‌شده موبایل",
                    ),
                ),
                (
                    "code_hash",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name="هش کد یک‌بارمصرف",
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="زمان انقضای کد",
                    ),
                ),
                (
                    "cooldown_until",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="پایان محدودیت ارسال مجدد",
                    ),
                ),
                (
                    "failed_attempts",
                    models.PositiveSmallIntegerField(
                        default=0,
                        verbose_name="تعداد تلاش ناموفق",
                    ),
                ),
                (
                    "send_lock_token",
                    models.UUIDField(
                        blank=True,
                        null=True,
                        verbose_name="شناسه قفل ارسال",
                    ),
                ),
                (
                    "send_lock_expires_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="انقضای قفل ارسال",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "وضعیت کد یک‌بارمصرف",
                "verbose_name_plural": "وضعیت کدهای یک‌بارمصرف",
            },
        ),
    ]
