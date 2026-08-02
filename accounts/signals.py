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
        Profile.objects.create(user=instance, phone_number="00000000000")


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """ذخیره خودکار پروفایل هنگام آپدیت کاربر"""
    if hasattr(instance, "profile"):
        instance.profile.save()
