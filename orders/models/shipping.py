# models.py (فایل مربوط به ShippingMethod)
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from decimal import Decimal  # Decimal رو import کن


class ShippingMethod(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="نام روش ارسال")
    description = models.TextField(blank=True, verbose_name="توضیحات")
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=0,  # تغییر از 2 به 0 برای نمایش اعداد صحیح تومان
        validators=[MinValueValidator(0)],
        verbose_name="هزینه ارسال (تومان)",
    )
    free_shipping_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=0,  # تغییر از 2 به 0
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="حداقل مبلغ سفارش برای ارسال رایگان (تومان)",
    )
    estimated_delivery_days = models.PositiveIntegerField(
        default=3,
        validators=[MinValueValidator(1)],
        verbose_name="زمان تخمینی تحویل (روز)",
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="فعال")
    created = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="آخرین بروزرسانی")

    class Meta:
        verbose_name = "روش ارسال"
        verbose_name_plural = "روش‌های ارسال"
        ordering = ["cost", "-is_active"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cost__gte=0), name="shipping_cost_gte_0"
            ),
            models.CheckConstraint(
                condition=models.Q(free_shipping_threshold__gte=0)
                | models.Q(free_shipping_threshold__isnull=True),
                name="shipping_free_shipping_threshold_gte_0",
            ),
        ]

    def __str__(self):
        # نمایش هزینه بدون اعشار در استرینگ نمایش
        return f"{self.name} ({self.cost} تومان)"

    def clean(self):
        super().clean()
        if self.cost < 0:
            raise ValidationError({"cost": "هزینه ارسال نمی‌تواند منفی باشد."})
        if (
            self.free_shipping_threshold is not None
            and self.free_shipping_threshold < 0
        ):
            raise ValidationError(
                {
                    "free_shipping_threshold": "حداقل مبلغ سفارش برای ارسال رایگان نمی‌تواند منفی باشد."
                }
            )

    def calculate_shipping_cost(self, cart_total):
        if (
            self.free_shipping_threshold is not None
            and self.free_shipping_threshold > 0
            and Decimal(str(cart_total)) >= self.free_shipping_threshold
        ):
            return Decimal(0)  # ارسال رایگان
        return self.cost  # در غیر این صورت، هزینه پایه اعمال می‌شود
