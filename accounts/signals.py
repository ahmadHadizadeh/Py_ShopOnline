from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from accounts.models.profile import Profile

# استفاده از get_user_model برای سازگاری کامل با مدل User سفارشی احتمالی
User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """ایجاد پروفایل در لحظه ثبت‌نام کاربر"""
    if created:
        phone = None
        # استخراج شماره موبایل در صورتی که نام کاربری به شکل شماره ساخته شده باشد
        if instance.username:
            if instance.username.startswith("user_09") and len(instance.username) == 16:
                candidate = instance.username.replace("user_", "")
                if not Profile.objects.filter(phone_number=candidate).exists():
                    phone = candidate
            elif instance.username.startswith("09") and len(instance.username) == 11:
                if not Profile.objects.filter(phone_number=instance.username).exists():
                    phone = instance.username

        Profile.objects.get_or_create(
            user=instance,
            defaults={
                "phone_number": phone,
                "is_verified": bool(phone),
            },
        )


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """ذخیره خودکار پروفایل هنگام آپدیت کاربر"""
    if hasattr(instance, "profile"):
        instance.profile.save()
