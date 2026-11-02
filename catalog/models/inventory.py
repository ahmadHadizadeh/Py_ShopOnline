from django.conf import settings
from django.db import models
from django.db.models import F, Q


class InventoryMovement(models.Model):
    """Auditable record of every committed product-stock movement."""

    class Source(models.TextChoices):
        MANUAL_ADJUSTMENT = "manual_adjustment", "اصلاح دستی موجودی"
        PHYSICAL_COUNT = "physical_count", "ثبت شمارش فیزیکی"
        ORDER_PAYMENT = "order_payment", "کاهش موجودی سفارش پرداخت‌شده"

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_movements",
        verbose_name="محصول",
    )
    product_name = models.CharField(max_length=250, verbose_name="نام محصول در زمان عملیات")
    delta = models.IntegerField(verbose_name="تغییر موجودی")
    quantity_before = models.PositiveIntegerField(verbose_name="موجودی قبل")
    quantity_after = models.PositiveIntegerField(verbose_name="موجودی بعد")
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        verbose_name="نوع عملیات",
    )
    reason = models.CharField(max_length=500, verbose_name="دلیل عملیات")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_movements",
        verbose_name="کاربر ثبت‌کننده",
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="نام ثبت‌کننده در زمان عملیات",
    )
    order_number = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="شماره سفارش مرتبط",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت")

    class Meta:
        verbose_name = "گردش موجودی"
        verbose_name_plural = "گردش‌های موجودی"
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(
                fields=["product", "-created_at"],
                name="cat_inv_product_created",
            ),
            models.Index(
                fields=["source", "-created_at"],
                name="cat_inv_source_created",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(delta=F("quantity_after") - F("quantity_before")),
                name="cat_inv_delta_match",
            )
        ]

    def __str__(self):
        return (
            f"{self.product_name}: {self.delta:+d} "
            f"({self.quantity_before} → {self.quantity_after})"
        )
