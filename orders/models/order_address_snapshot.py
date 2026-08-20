# order/models/OrderAddressSnapshot
from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderAddressSnapshot(models.Model):
    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="address_snapshot",
        verbose_name=_("سفارش"),
    )
    recipient_name = models.CharField(max_length=100, verbose_name=_("نام گیرنده"))
    recipient_mobile = models.CharField(max_length=15, verbose_name=_("موبایل گیرنده"))
    postal_code = models.CharField(max_length=10, verbose_name=_("کد پستی"))
    province = models.CharField(max_length=50, verbose_name=_("استان"))
    city = models.CharField(max_length=50, verbose_name=_("شهر"))
    address_line = models.CharField(max_length=255, verbose_name=_("آدرس دقیق"))
    created = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))
    updated = models.DateTimeField(auto_now=True, verbose_name=_("زمان بروزرسانی"))

    class Meta:
        verbose_name = _("اسنپ‌شات آدرس سفارش")
        verbose_name_plural = _("اسنپ‌شات‌های آدرس سفارش")
        ordering = ["-created"]

    def __str__(self):
        return f"{_('آدرس سفارش')} {self.order.order_number} {_('برای')} {self.recipient_name}"
