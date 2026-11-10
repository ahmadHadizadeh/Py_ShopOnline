from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0010_storefront_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="storefrontsettings",
            name="hero_title_line_1",
            field=models.CharField(
                default="محصول موردنیازتان، فقط چند کلیک با شما فاصله دارد.",
                help_text="متن اصلی بنر صفحه اصلی؛ حداکثر ۱۶۰ نویسه.",
                max_length=160,
                verbose_name="خط اول عنوان بنر صفحه اصلی",
            ),
        ),
        migrations.AddField(
            model_name="storefrontsettings",
            name="hero_title_line_2",
            field=models.CharField(
                default="در آنلاین‌شاپ احمد، ساده و آگاهانه انتخاب کنید.",
                help_text="متن تأکیدی زیر خط اول؛ حداکثر ۱۶۰ نویسه.",
                max_length=160,
                verbose_name="خط دوم عنوان بنر صفحه اصلی",
            ),
        ),
    ]
