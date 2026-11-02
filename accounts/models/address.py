from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, router, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

class Address(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
        verbose_name="کاربر",
    )
    recipient_name = models.CharField(
        max_length=100,
        verbose_name="نام گیرنده",
    )
    phone_number = models.CharField(
        max_length=15,
        verbose_name="شماره تماس",
        help_text="مثال: 09123456789",
    )
    postal_code = models.CharField(
        max_length=10,
        verbose_name="کد پستی",
        help_text="کد پستی ۱۰ رقمی",
    )
    province = models.CharField(
        max_length=50,
        verbose_name="استان",
    )
    city = models.CharField(
        max_length=50,
        verbose_name="شهر",
    )
    address_line = models.CharField(
        max_length=255,
        verbose_name="آدرس پستی",
        help_text="آدرس دقیق پستی شامل نام کوچه، پلاک و واحد",
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name="آدرس پیش‌فرض",
        help_text="این آدرس به عنوان پیش‌فرض برای سفارش‌ها استفاده شود.",
    )
    created = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="آخرین بروزرسانی")

    class Meta:
        verbose_name = "آدرس"
        verbose_name_plural = "آدرس‌ها"
        ordering = ["-is_default", "-updated"] # اولویت با آدرس پیش فرض و بعد آدرس های جدیدتر
        indexes = [
            models.Index(fields=["user", "is_default"]),
            models.Index(fields=["-updated"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="unique_default_address_per_user",
            ),
        ]

    def __str__(self):
        # نمایش مختصر آدرس برای راحتی در ادمین و لاگ‌ها
        return f"{self.recipient_name} - {self.province}, {self.city} [{self.pk}]"

    def clean(self):
        super().clean()
        # اعتبارسنجی اولیه برای شماره تماس
        if not self.phone_number or not self.phone_number.isdigit() or len(self.phone_number) < 10:
             raise ValidationError({'phone_number': 'شماره تماس باید حداقل ۱۰ رقم و شامل اعداد باشد.'})
        # اعتبارسنجی اولیه برای کد پستی
        if not self.postal_code or not self.postal_code.isdigit() or len(self.postal_code) != 10:
            raise ValidationError({'postal_code': 'کد پستی باید دقیقاً ۱۰ رقم و شامل اعداد باشد.'})
        # اعتبارسنجی برای نام گیرنده و آدرس
        if not self.recipient_name or not self.address_line:
            raise ValidationError("نام گیرنده و آدرس پستی نمی‌توانند خالی باشند.")
        # اعتبارسنجی برای استان و شهر
        if not self.province or not self.city:
            raise ValidationError("استان و شهر نمی‌توانند خالی باشند.")


    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            get_user_model()._default_manager.using(using).select_for_update().only(
                "pk"
            ).get(pk=self.user_id)

            update_fields = kwargs.get("update_fields")
            default_is_written = (
                update_fields is None or "is_default" in update_fields
            )
            if default_is_written and self.is_default:
                Address.objects.using(using).filter(user_id=self.user_id).exclude(
                    pk=self.pk
                ).update(
                    is_default=False,
                    updated=timezone.now(),
                )

            super().save(*args, **kwargs)
