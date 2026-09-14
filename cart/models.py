# cart/models.py
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from catalog.models import Product


class Cart(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ORDERED = "ordered"
    STATUS_ABANDONED = "abandoned"

    STATUS_CHOICES = (
        (STATUS_ACTIVE, "فعال"),
        (STATUS_ORDERED, "تبدیل‌شده به سفارش"),
        (STATUS_ABANDONED, "رهاشده"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="carts",
        null=True,
        blank=True,
        verbose_name="کاربر",
    )
    session_key = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="کلید سشن",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
        verbose_name="وضعیت",
    )
    created = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="آخرین بروزرسانی")

    class Meta:
        verbose_name = "سبد خرید"
        verbose_name_plural = "سبدهای خرید"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["session_key", "status"]),
            models.Index(fields=["-updated"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="active", user__isnull=False),
                name="unique_active_cart_per_user",
            ),
            models.UniqueConstraint(
                fields=["session_key"],
                condition=models.Q(status="active", session_key__isnull=False),
                name="unique_active_cart_per_session",
            ),
        ]

    def __str__(self):
        if self.user_id:
            return f"Cart #{self.pk} - {self.user}"
        return f"Guest Cart #{self.pk}"

    @property
    def active_items(self):
        return self.items.filter(status="active")

    @property
    def saved_items(self):
        return self.items.filter(status="saved")

    @property
    def total_items(self):
        return sum(item.quantity for item in self.active_items)

    @property
    def total_price(self):
        return sum(item.subtotal for item in self.active_items)

    @property
    def total_old_price(self):
        return sum(item.total_old_price for item in self.active_items)

    @property
    def discount_amount(self):
        return self.total_old_price - self.total_price

    def is_guest_cart(self):
        return self.user_id is None


class CartItem(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_SAVED = "saved"
    STATUS_CHOICES = (
        ("active", "فعال"),
        ("saved", "ذخیره شده"),
    )

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="سبد خرید",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="cart_items",
        verbose_name="محصول",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cart_items",
        verbose_name="تنوع محصول",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name="تعداد",
    )
    unit_price_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="قیمت ثبت‌شده در سبد",
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="active",
        verbose_name="وضعیت",
    )
    created = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="آخرین بروزرسانی")

    class Meta:
        verbose_name = "آیتم سبد خرید"
        verbose_name_plural = "آیتم‌های سبد خرید"
        indexes = [
            models.Index(fields=["cart", "product", "variant"]),
            models.Index(fields=["product"]),
            models.Index(fields=["variant"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product"],
                condition=models.Q(variant__isnull=True),
                name="unique_product_without_variant_per_cart",
            ),
            models.UniqueConstraint(
                fields=["cart", "product", "variant"],
                condition=models.Q(variant__isnull=False),
                name="unique_product_variant_per_cart",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="cart_item_quantity_gte_1",
            ),
        ]

    def __str__(self):
        suffix = f" / {self.variant}" if self.variant_id else ""
        return f"{self.product}{suffix} × {self.quantity}"

    @property
    def subtotal(self):
        return self.unit_price_snapshot * self.quantity

    @property
    def unit_old_price(self):
        if self.product.old_price and self.product.old_price > self.unit_price_snapshot:
            return self.product.old_price
        return self.unit_price_snapshot

    @property
    def total_old_price(self):
        return self.unit_old_price * self.quantity

    @property
    def discount_amount(self):
        return self.total_old_price - self.subtotal

    @property
    def has_discount(self):
        return self.discount_amount > 0

    def clean(self):
        super().clean()

        if self.variant_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": "واریانت انتخاب‌شده متعلق به این محصول نیست."})

        if self.status == "active":
            if not self.product.is_available:
                raise ValidationError({"product": "این محصول در حال حاضر قابل خرید نیست."})
            if self.quantity > self.product.stock:
                raise ValidationError(
                    {"quantity": "تعداد انتخاب‌شده بیشتر از موجودی محصول است."}
                )

    def save(self, *args, **kwargs):
        if self.unit_price_snapshot is None:
            self.unit_price_snapshot = self.product.price + (
                self.variant.price_adjustment if self.variant_id else Decimal("0")
            )
        super().save(*args, **kwargs)
