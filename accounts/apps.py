from django.apps import AppConfig


STORE_MANAGER_GROUP_NAME = "مدیر فروشگاه"


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "مدیریت کاربران"

    def ready(self):
        # این import حیاتی است تا سیگنال‌ها در زمان بالا آمدن جنگو ثبت شوند
        import accounts.signals
