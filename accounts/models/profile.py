from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import random


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("کاربر"),
    )
    phone_number = models.CharField(
        max_length=15, unique=True, db_index=True, verbose_name=_("شماره موبایل")
    )
    is_verified = models.BooleanField(default=False, verbose_name=_("تایید شده"))
    otp_code = models.CharField(
        max_length=6, null=True, blank=True, verbose_name=_("کد تایید")
    )
    otp_created_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("زمان ایجاد کد")
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("تاریخ عضویت"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین بروزرسانی"))

    class Meta:
        verbose_name = _("پروفایل")
        verbose_name_plural = _("پروفایل‌ها")
        indexes = [
            models.Index(fields=["phone_number"]),
        ]

    def __str__(self):
        return f"{self.phone_number}"

    def generate_otp(self):
        """تولید کد OTP و ثبت زمان"""
        self.otp_code = str(random.randint(100000, 999999))
        self.otp_created_at = timezone.now()
        self.save(update_fields=["otp_code", "otp_created_at"])
        return self.otp_code

    def is_otp_valid(self, code):
        """اعتبارسنجی کد با رعایت زمان انقضا (مثلاً ۳ دقیقه)"""
        if self.otp_code != code:
            return False

        # انقضای ۳ دقیقه‌ای
        if (
            self.otp_created_at
            and (timezone.now() - self.otp_created_at).total_seconds() > 180
        ):
            return False

        return True
