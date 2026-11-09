from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0008_protect_product_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="image",
            field=models.ImageField(
                blank=True,
                default="",
                upload_to="categories/%Y/%m/%d",
                verbose_name="تصویر دسته‌بندی",
            ),
        ),
    ]
