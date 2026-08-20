# orders/models/orders.py
from decimal import Decimal
from uuid import uuid4
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار ثبت"
        PLACED = "placed", "ثبت شده"
        PAID = "paid", "پرداخت شده"
        PROCESSING = "processing", "در حال پردازش"
        COMPLETED = "completed", "تکمیل شده"
        CANCELLED = "cancelled", "لغو شده"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="کاربر",
    )
    cart = models.ForeignKey(
        "cart.Cart",
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
        verbose_name="سبد مرجع",
    )
    # --- فیلد ارتباطی با ShippingMethod ---
    shipping_method = models.ForeignKey(
        "orders.ShippingMethod",  # مسیر صحیح مدل ShippingMethod شما
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="روش ارسال",
    )
    # -------------------------------------

    order_number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        verbose_name="شماره سفارش",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="وضعیت",
    )

    subtotal_amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,  # دقت صفر برای تومان
        default=Decimal("0"),
        verbose_name="جمع مبلغ کالاها",
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,  # دقت صفر برای تومان
        default=Decimal("0"),
        verbose_name="مبلغ تخفیف",
    )
    shipping_amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,  # دقت صفر برای تومان
        default=Decimal("0"),
        verbose_name="هزینه ارسال",
    )
    final_amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,  # دقت صفر برای تومان
        default=Decimal("0"),
        verbose_name="مبلغ نهایی",
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان پرداخت",
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان لغو",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان تکمیل",
    )

    customer_note = models.TextField(
        blank=True,
        verbose_name="یادداشت مشتری",
    )
    admin_note = models.TextField(
        blank=True,
        verbose_name="یادداشت ادمین",
    )
    stock_reduced = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="زمان بروزرسانی")

    class Meta:
        verbose_name = "سفارش"
        verbose_name_plural = "سفارش‌ها"
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["created"]),
        ]

    def __str__(self):
        return self.order_number

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self.generate_order_number()

        # --- محاسبه هزینه ارسال و مبلغ نهایی ---
        current_shipping_cost = Decimal("0")
        if self.shipping_method:
            try:
                # استفاده از متد calculate_shipping_cost از مدل ShippingMethod
                # تبدیل به Decimal با دقت 0 برای هماهنگی با فیلدهای مالی سفارش
                calculated_cost = self.shipping_method.calculate_shipping_cost(
                    self.subtotal_amount
                )
                current_shipping_cost = Decimal(str(calculated_cost)).quantize(
                    Decimal("0")
                )
            except Exception as e:
                # لاگ خطا در صورت بروز مشکل در محاسبه هزینه ارسال
                print(
                    f"Warning: Could not calculate shipping cost for order {self.order_number}. Error: {e}"
                )
                current_shipping_cost = Decimal("0")

        self.shipping_amount = current_shipping_cost
        self.final_amount = max(
            Decimal("0"),
            self.subtotal_amount - self.discount_amount + self.shipping_amount,
        )
        # --------------------------------------------

        super().save(*args, **kwargs)

    @transaction.atomic
    def reduce_item_stock(self):
        if self.stock_reduced:
            return

        # اطمینان از وجود relation 'items' و 'product'
        # در صورت نیاز، select_related یا prefetch_related را برای performance اضافه کنید
        order_items = self.items.select_related("product").all().select_for_update()

        if not order_items.exists():
            self.stock_reduced = True
            Order.objects.filter(pk=self.pk).update(stock_reduced=True)
            return

        for item in order_items:
            product = item.product
            if product.stock < item.quantity:
                raise ValueError(
                    f"موجودی کافی نیست: {product.name}. موجودی: {product.stock}, مقدار: {item.quantity}"
                )

            product.stock -= item.quantity
            if product.stock == 0:
                product.is_available_status = False
            # بروزرسانی مستقیم در دیتابیس برای کارایی بیشتر
            product.save(update_fields=["stock", "is_available_status"])

        # بروزرسانی وضعیت و زمان پرداخت فقط در صورتی که قبلاً انجام نشده باشد
        if self.status == Order.Status.PENDING or self.status == Order.Status.PLACED:
            self.status = Order.Status.PAID
            if not self.paid_at:
                self.paid_at = timezone.now()

        # استفاده از update برای کارایی در صورتی که فقط چند فیلد تغییر کند
        Order.objects.filter(pk=self.pk).update(
            stock_reduced=True, status=self.status, paid_at=self.paid_at
        )

    @staticmethod
    def generate_order_number():
        return f"ORD-{uuid4().hex[:12].upper()}"
