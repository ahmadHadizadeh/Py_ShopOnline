# catalog/models/Product
from django.db import models, transaction
from django.urls import reverse
from catalog.utils import generate_unique_slug
from catalog.models import Category
from django.db.models import Q


class Product(models.Model):
    name = models.CharField(max_length=250, verbose_name="نام فارسی")
    name_en = models.CharField(
        max_length=200, blank=True, null=True, verbose_name="نام انگلیسی محصول"
    )
    brand = models.CharField(max_length=100, blank=True, null=True, verbose_name="برند")
    slug = models.SlugField(max_length=250, unique=True, allow_unicode=True)
    image = models.ImageField(blank=True, upload_to="products/%Y/%m/%d")
    description = models.TextField(blank=True, verbose_name="توضیحات کوتاه")
    review = models.TextField(blank=True, verbose_name="نقد و بررسی تخصصی")
    price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="قیمت فعلی (تومان)"
    )
    old_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name="قیمت قبلی (تومان)",
    )
    stock = models.PositiveIntegerField(default=0, verbose_name="موجودی")
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    is_available_status = models.BooleanField(default=True, verbose_name="موجود بودن")
    highlights = models.TextField(blank=True, verbose_name="ویژگی‌های کلیدی")
    guarantee = models.CharField(
        max_length=200, default="۱۸ ماه گارانتی شرکتی", verbose_name="گارانتی"
    )
    delivery_info = models.CharField(
        max_length=200, default="ارسال سریع به سراسر کشور", verbose_name="اطلاعات ارسال"
    )
    created = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated = models.DateTimeField(auto_now=True, verbose_name="تاریخ بروزرسانی")
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="products"
    )
    related_products = models.ManyToManyField(
        "self", blank=True, verbose_name="محصولات مشابه"
    )

    class Meta:
        verbose_name = "محصول"
        verbose_name_plural = "محصولات"
        ordering = ["-created"]  # مرتب‌سازی بر اساس زمان ایجاد، جدیدترین اول
        indexes = [
            # ایندکس های تکراری حذف شدند
            models.Index(fields=["id", "slug"]),  # برای جستجوی سریع بر اساس id و slug
            # models.Index(fields=["slug"]), # این ایندکس با id, slug پوشش داده شده است
            models.Index(
                fields=["is_active", "is_available_status"]
            ),  # برای فیلتر محصولات فعال و در دسترس
            models.Index(
                fields=["category", "is_active"]
            ),  # برای فیلتر محصولات بر اساس دسته بندی و وضعیت فعال بودن
            # models.Index(fields=["-created"]), # این ایندکس با ordering پوشش داده شده است
        ]

        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="product_price_gte_0"
            ),
            models.CheckConstraint(
                condition=models.Q(stock__gte=0), name="product_stock_gte_0"
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = generate_unique_slug(self, self.name)
        self.update_availability_status_on_save()
        super().save(*args, **kwargs)

    def update_availability_status_on_save(self):
        self.is_available_status = self.is_active and self.stock > 0

    @property
    def is_available(self):
        return self.is_active and self.is_available_status and self.stock > 0

    @property
    def discount_percent(self):
        if not self.old_price or self.old_price <= self.price:
            return 0
        return round(((self.old_price - self.price) / self.old_price) * 100)

    def get_absolute_url(self):
        return reverse("catalog:product_detail", args=[self.slug])
