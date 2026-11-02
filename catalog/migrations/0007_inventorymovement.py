# Generated for the auditable product inventory ledger.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_updatepayments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryMovement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "product_name",
                    models.CharField(
                        max_length=250,
                        verbose_name="نام محصول در زمان عملیات",
                    ),
                ),
                ("delta", models.IntegerField(verbose_name="تغییر موجودی")),
                (
                    "quantity_before",
                    models.PositiveIntegerField(verbose_name="موجودی قبل"),
                ),
                (
                    "quantity_after",
                    models.PositiveIntegerField(verbose_name="موجودی بعد"),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual_adjustment", "اصلاح دستی موجودی"),
                            ("physical_count", "ثبت شمارش فیزیکی"),
                            ("order_payment", "کاهش موجودی سفارش پرداخت‌شده"),
                        ],
                        max_length=32,
                        verbose_name="نوع عملیات",
                    ),
                ),
                (
                    "reason",
                    models.CharField(max_length=500, verbose_name="دلیل عملیات"),
                ),
                (
                    "actor_label",
                    models.CharField(
                        blank=True,
                        max_length=150,
                        verbose_name="نام ثبت‌کننده در زمان عملیات",
                    ),
                ),
                (
                    "order_number",
                    models.CharField(
                        blank=True,
                        max_length=40,
                        verbose_name="شماره سفارش مرتبط",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت"),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inventory_movements",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="کاربر ثبت‌کننده",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inventory_movements",
                        to="catalog.product",
                        verbose_name="محصول",
                    ),
                ),
            ],
            options={
                "verbose_name": "گردش موجودی",
                "verbose_name_plural": "گردش‌های موجودی",
                "ordering": ("-created_at", "-pk"),
                "indexes": [
                    models.Index(
                        fields=["product", "-created_at"],
                        name="cat_inv_product_created",
                    ),
                    models.Index(
                        fields=["source", "-created_at"],
                        name="cat_inv_source_created",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(
                            delta=F("quantity_after") - F("quantity_before")
                        ),
                        name="cat_inv_delta_match",
                    )
                ],
            },
        ),
    ]
