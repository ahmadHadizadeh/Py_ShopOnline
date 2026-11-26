from django.db import migrations, models


def backfill_variant_prices(apps, schema_editor):
    Variant = apps.get_model("catalog", "ProductVariant")
    db = schema_editor.connection.alias

    queryset = (
        Variant.objects.using(db)
        .select_related("product")
        .order_by("pk")
    )

    batch = []
    for variant in queryset.iterator(chunk_size=500):
        product = variant.product
        final_price = product.price + variant.price_adjustment

        if final_price < 0:
            raise RuntimeError(
                f"Negative final price for ProductVariant ID {variant.pk}"
            )

        variant.price = final_price
        variant.price_adjustment = final_price - product.price
        batch.append(variant)

        if len(batch) >= 500:
            Variant.objects.using(db).bulk_update(
                batch, ["price", "price_adjustment"], batch_size=500
            )
            batch.clear()

    if batch:
        Variant.objects.using(db).bulk_update(
            batch, ["price", "price_adjustment"], batch_size=500
        )


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_storefront_brand_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="productvariant",
            name="price",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=12,
                null=True,
                verbose_name="قیمت نهایی (تومان)",
                help_text="قیمت مستقل این تنوع؛ قیمت محصول به آن اضافه نمی‌شود.",
            ),
        ),
        migrations.AlterField(
            model_name="productvariant",
            name="price_adjustment",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                editable=False,
                max_digits=12,
                verbose_name="تغییر قیمت (سازگاری نسخه قبلی)",
            ),
        ),
        migrations.RunPython(
            backfill_variant_prices,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="productvariant",
            name="price",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=12,
                verbose_name="قیمت نهایی (تومان)",
                help_text="قیمت مستقل این تنوع؛ قیمت محصول به آن اضافه نمی‌شود.",
            ),
        ),
        migrations.AddConstraint(
            model_name="productvariant",
            constraint=models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="variant_final_price_gte_0",
            ),
        ),
    ]
