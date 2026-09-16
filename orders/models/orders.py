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

        # Shipping is calculated by OrderService.calculate_order_totals().
        # Order.save() must not recalculate shipping or swallow shipping errors.
        self.final_amount = max(
            Decimal("0"),
            self.subtotal_amount - self.discount_amount + self.shipping_amount,
        )

        super().save(*args, **kwargs)

    @transaction.atomic
    def reduce_item_stock(self):
        if self.stock_reduced:
            return
    
        order_items = list(self.items.select_related("product").select_for_update())
    
        if not order_items:
            Order.objects.filter(pk=self.pk).update(
                stock_reduced=True,
            )
            self.stock_reduced = True
            return
    
        # ابتدا تمام موجودی‌ها را قبل از کوچک‌ترین تغییر بررسی می‌کنیم.
        # بنابراین در صورت کمبود موجودی هیچ محصولی نصفه‌نیمه کاهش پیدا نمی‌کند.
        # مجموع quantity هر Product را محاسبه می‌کنیم؛ یک Product ممکن است به‌دلیل
        # Variantهای مختلف در چند OrderItem تکرار شده باشد.
        required_quantities = {}
        for item in order_items:
            if item.product_id is None:
                raise ValueError(f"محصول آیتم سفارش حذف شده است: order_item={item.pk}")

            required_quantities[item.product_id] = (
                required_quantities.get(item.product_id, 0) + item.quantity
            )

        # همه Productها را قبل از کوچک‌ترین تغییر lock و validate می‌کنیم.
        locked_products = {}
        for product_id, required_quantity in required_quantities.items():
            sample_item = next(
                item for item in order_items if item.product_id == product_id
            )
            product = type(sample_item.product).objects.select_for_update().get(
                pk=product_id
            )
            locked_products[product_id] = product

            if product.stock < required_quantity:
                raise ValueError(
                    f"موجودی کافی نیست: {product.name}. "
                    f"موجودی: {product.stock}, مقدار موردنیاز: {required_quantity}"
                )

        # فقط پس از اعتبارسنجی مجموع موجودی، برای هر Product یک‌بار کاهش انجام می‌شود.
        for product_id, required_quantity in required_quantities.items():
            product = locked_products[product_id]
            product.stock -= required_quantity

            if product.stock == 0:
                product.is_available_status = False

            product.save(
                update_fields=[
                    "stock",
                    "is_available_status",
                ]
            )

        if self.status in (
            Order.Status.PENDING,
            Order.Status.PLACED,
        ):
            self.status = Order.Status.PAID
    
            if not self.paid_at:
                self.paid_at = timezone.now()
    
        Order.objects.filter(pk=self.pk).update(
            stock_reduced=True,
            status=self.status,
            paid_at=self.paid_at,
        )
    
        self.stock_reduced = True

    @staticmethod
    def generate_order_number():
        return f"ORD-{uuid4().hex[:12].upper()}"
