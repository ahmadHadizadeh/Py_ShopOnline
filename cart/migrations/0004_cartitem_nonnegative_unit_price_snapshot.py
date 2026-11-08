from django.db import migrations, models


def reject_negative_cart_item_snapshots(apps, schema_editor):
    CartItem = apps.get_model("cart", "CartItem")
    invalid_count = CartItem.objects.filter(unit_price_snapshot__lt=0).count()
    if invalid_count:
        raise RuntimeError(
            "Cannot add cart_item_unit_price_snapshot_gte_0: "
            f"{invalid_count} existing CartItem row(s) have a negative "
            "unit_price_snapshot. No rows were modified. Review and resolve "
            "these records explicitly, then rerun migrations."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("cart", "0003_remove_cartitem_unique_product_per_cart_and_more"),
    ]

    operations = [
        migrations.RunPython(
            reject_negative_cart_item_snapshots,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.CheckConstraint(
                condition=models.Q(unit_price_snapshot__gte=0),
                name="cart_item_unit_price_snapshot_gte_0",
            ),
        ),
    ]
