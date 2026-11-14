from django.db import models


class OTPChallenge(models.Model):
    """One active OTP state per keyed phone identifier.

    The original phone number is deliberately not stored in this table.
    """

    phone_hash = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="شناسه هش‌شده موبایل",
    )
    code_hash = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="هش کد یک‌بارمصرف",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان انقضای کد",
    )
    cooldown_until = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="پایان محدودیت ارسال مجدد",
    )
    failed_attempts = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="تعداد تلاش ناموفق",
    )
    send_lock_token = models.UUIDField(
        null=True,
        blank=True,
        verbose_name="شناسه قفل ارسال",
    )
    send_lock_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="انقضای قفل ارسال",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "وضعیت کد یک‌بارمصرف"
        verbose_name_plural = "وضعیت کدهای یک‌بارمصرف"

    def __str__(self):
        return f"OTP challenge {self.pk}"
