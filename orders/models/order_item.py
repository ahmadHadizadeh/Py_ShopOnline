from django.db import models
from django.urls import reverse
from catalog.models.product import Product
from decimal import Decimal


class OrderItem(models.Model):
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="سفارش",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )
    variant_id = models.BigIntegerField(
        null=True, blank=True, verbose_name="شناسه واریانت"
    )
    product_name = models.CharField(max_length=255, verbose_name="نام محصول")
    variant_name = models.CharField(
        max_length=255, blank=True, verbose_name="نام واریانت"
    )
    sku = models.CharField(max_length=100, blank=True, null=True, verbose_name="SKU")
    quantity = models.PositiveIntegerField(default=1, verbose_name="تعداد")
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="قیمت واحد",
    )
    subtotal_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="جمع قیمت سطر",
    )
    created = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="زمان بروزرسانی")

    class Meta:
        verbose_name = "آیتم سفارش"
        verbose_name_plural = "آیتم‌های سفارش"
        ordering = ["created"]
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["variant_id"]),
            # ایندکس های مربوط به product و product_id حذف شده اند،
            # زیرا Django به طور خودکار ایندکس را برای ForeignKey ایجاد می کند.
        ]

    def __str__(self):
        # برای نمایش بهتر، وضعیت order را چک می کنیم
        order_num_display = self.order.order_number if self.order else "N/A"
        return f"{self.quantity} of {self.product_name} (Order: {order_num_display})"

    def save(self, *args, **kwargs):
        if self.quantity is not None and self.unit_price is not None:
            self.subtotal_price = Decimal(str(self.quantity)) * Decimal(
                str(self.unit_price)
            )
        else:
            self.subtotal_price = Decimal("0")

        super().save(*args, **kwargs)

    def set_product(self, product_instance):
        if product_instance:
            self.product = product_instance
            self.product_name = product_instance.name
            self.sku = getattr(
                product_instance, "sku", None
            )  # استفاده از getattr برای امنیت بیشتر
            self.unit_price = product_instance.price
        else:
            # پاک کردن اطلاعات در صورت نبودن محصول
            self.product = None
            self.product_name = ""
            self.sku = None
            self.unit_price = models.DecimalField(0, max_digits=12, decimal_places=2)
