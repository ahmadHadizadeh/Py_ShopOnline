from django.conf import settings
from django.db import models, transaction

from django.utils.translation import gettext_lazy as _
from .orders import Order


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار پرداخت"
        SUCCESS = "success", "پرداخت موفق"
        FAILED = "failed", "پرداخت ناموفق"
        REFUNDED = "refunded", "مرجوع شده"
        CANCELLED = "cancelled", "لغو شده"

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="payment",
        verbose_name="سفارش",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="کاربر",
        editable=False,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        verbose_name="مبلغ پرداخت",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="وضعیت پرداخت",
    )
    gateway_name = models.CharField(
        max_length=50, blank=True, verbose_name="نام درگاه پرداخت"
    )
    transaction_code = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        blank=True,
        null=True,
        verbose_name="کد تراکنش درگاه",
    )
    reference_code = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        blank=True,
        null=True,
        verbose_name="کد رهگیری پرداخت",
    )
    gateway_response = models.TextField(blank=True, verbose_name="پاسخ درگاه")
    created = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد تراکنش")
    updated = models.DateTimeField(auto_now=True, verbose_name="زمان بروزرسانی تراکنش")

    class Meta:
        verbose_name = "پرداخت"
        verbose_name_plural = "پرداخت‌ها"
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["gateway_name"]),
        ]

    def __str__(self):
        return f"{self.get_status_display()} - {self.order.order_number}"

    @transaction.atomic
    def update_status_and_order(
        self,
        new_status,
        transaction_id=None,
        reference_id=None,
        gateway_response=None,
    ):
        """
        Final method to synchronize payment success with Order's reduce_item_stock.
        """
        if transaction_id:
            self.transaction_code = transaction_id
        if reference_id:
            self.reference_code = reference_id
        if gateway_response:
            self.gateway_response = gateway_response

        if self.status == new_status:
            return self.status, False

        self.status = new_status

        if new_status == self.Status.SUCCESS:
            try:
                # This method in Order handles: stock reduction, status=PAID, and paid_at.
                self.order.reduce_item_stock()
            except ValueError as e:
                self.status = self.Status.FAILED
                self.order.status = Order.Status.CANCELLED
                self.gateway_response = str(e)
                self.order.save(update_fields=["status"])

        elif new_status in [self.Status.FAILED, self.Status.CANCELLED]:
            self.order.status = Order.Status.CANCELLED
            self.order.save(update_fields=["status"])

        self.save(
            update_fields=[
                "status",
                "transaction_code",
                "reference_code",
                "gateway_response",
            ]
        )
        return self.status, True

    def save(self, *args, **kwargs):
        if self.order and not self.user:
            self.user = self.order.user
        if not self.amount and self.order:
            self.amount = self.order.final_amount
        super().save(*args, **kwargs)
