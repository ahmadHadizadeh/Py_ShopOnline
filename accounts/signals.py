from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from accounts.apps import STORE_MANAGER_GROUP_NAME
from accounts.models.profile import Profile


User = get_user_model()

# Fixed permissions for the one store operator. Keep identity, cart,
# customer-address, shipping-configuration, payment-write and destructive
# product/order permissions out of this list.
STORE_MANAGER_PERMISSION_CODENAMES = {
    "catalog": (
        "add_product", "change_product",
        "add_category", "change_category",
        "add_productvariant", "change_productvariant",
        "add_productimage", "change_productimage", "delete_productimage",
        "add_productspecification", "change_productspecification",
        "delete_productspecification",
        "change_review",
        "view_inventorymovement",
    ),
    "orders": (
        "change_order",
        "view_payment",
        "view_orderitem",
        "view_orderaddresssnapshot",
    ),
}


@receiver(post_migrate, dispatch_uid="accounts.ensure_store_manager_group")
def ensure_store_manager_group(sender, using, **kwargs):
    """Create the fixed role only after Django has created app permissions.

    The orders app is last among project apps in core/settings.py. This receiver
    is registered by AccountsConfig.ready(), after auth's permission receiver.
    A pre-existing same-name group with a different permission set fails closed
    rather than having its assignments silently rewritten.
    """
    if sender.label != "orders":
        return

    expected = {
        (app_label, codename)
        for app_label, codenames in STORE_MANAGER_PERMISSION_CODENAMES.items()
        for codename in codenames
    }
    permission_filter = None
    for app_label, codenames in STORE_MANAGER_PERMISSION_CODENAMES.items():
        condition = Q(content_type__app_label=app_label, codename__in=codenames)
        permission_filter = condition if permission_filter is None else permission_filter | condition

    permissions = list(
        Permission.objects.using(using)
        .filter(permission_filter)
        .select_related("content_type")
    )
    found = {
        (permission.content_type.app_label, permission.codename)
        for permission in permissions
    }
    missing = sorted(expected - found)
    if missing:
        missing_text = ", ".join(f"{app}.{codename}" for app, codename in missing)
        raise RuntimeError(
            "مجوزهای لازم برای ساخت گروه «مدیر فروشگاه» پیدا نشدند: "
            f"{missing_text}"
        )

    with transaction.atomic(using=using):
        group, created = Group.objects.using(using).get_or_create(
            name=STORE_MANAGER_GROUP_NAME
        )
        if created:
            group.permissions.set(permissions)
            return

        existing = set(
            group.permissions.using(using).values_list(
                "content_type__app_label", "codename"
            )
        )
        if existing != expected:
            raise RuntimeError(
                "گروه «مدیر فروشگاه» از قبل وجود دارد، اما مجوزهای آن با نقش "
                "ثابت پروژه مطابقت ندارد. برای جلوگیری از تغییر ناخواسته "
                "دسترسی‌ها، ابتدا گروه موجود باید ممیزی شود."
            )


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """ایجاد پروفایل در لحظه ثبت‌نام کاربر"""
    if created:
        phone = None
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
            defaults={"phone_number": phone, "is_verified": bool(phone)},
        )


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """ذخیره خودکار پروفایل هنگام آپدیت کاربر"""
    if hasattr(instance, "profile"):
        instance.profile.save()
