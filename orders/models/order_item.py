# orders/models/OrderItem
from django.db import models
from django.urls import reverse
from catalog.models.product import Product
from decimal import Decimal


class OrderItem(models.Model):
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="items",
        related_query_name="order_item",
        verbose_name="سفارش",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="محصول",
    )
    variant_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name="شناسه واریانت",
    )
    product_name = models.CharField(max_length=255, verbose_name="نام محصول")
    variant_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="نام واریانت",
    )
    sku = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="SKU",
    )
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
        ]

    def __str__(self):
        order_num = self.order.order_number if self.order_id and self.order else "N/A"
        return f"{self.quantity} × {self.product_name} (Order: {order_num})"

    def save(self, *args, **kwargs):
        quantity = Decimal(str(self.quantity or 0))
        unit_price = Decimal(str(self.unit_price or 0))
        self.subtotal_price = quantity * unit_price
        super().save(*args, **kwargs)

    @property
    def discount_amount(self):
        product = self.product
        if not product:
            return Decimal("0")

        old_price = getattr(product, "old_price", None)
        if old_price is None:
            return Decimal("0")

        current_unit_price = Decimal(str(self.unit_price or 0))
        if old_price <= current_unit_price:
            return Decimal("0")

        quantity = Decimal(str(self.quantity or 0))
        return (Decimal(str(old_price)) - current_unit_price) * quantity

    def set_product(self, product_instance):
        if not product_instance:
            self.product = None
            self.product_name = ""
            self.sku = None
            self.unit_price = Decimal("0")
            return

        self.product = product_instance
        self.product_name = product_instance.name
        self.sku = getattr(product_instance, "sku", None)
        self.unit_price = product_instance.price
