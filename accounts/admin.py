from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import format_html

from core.admin_formatting import AdminDisplayFormattingMixin

from .apps import STORE_MANAGER_GROUP_NAME
from .models.address import Address
from .models.profile import Profile


User = get_user_model()


def ensure_store_manager_assignment_available(user, manager_group):
    """Guard a new fixed-role assignment while its caller owns a DB transaction."""
    if not user.pk:
        raise ValidationError("ابتدا حساب کاربری را ذخیره کنید.")
    if manager_group.name != STORE_MANAGER_GROUP_NAME:
        raise ValidationError("فقط نقش ثابت مدیر فروشگاه قابل تخصیص است.")
    if user.is_superuser:
        raise ValidationError("صاحب فروشگاه نباید نقش مدیر فروشگاه بگیرد.")

    if user.user_permissions.exists():
        raise ValidationError(
            "این حساب مجوز مستقیم دارد؛ پیش از واگذاری نقش مدیر، دسترسی‌های قبلی را ممیزی کنید."
        )
    if (
        user.groups.exclude(pk=manager_group.pk)
        .filter(permissions__isnull=False)
        .exists()
    ):
        raise ValidationError(
            "این حساب عضو گروه دارای مجوز دیگری است؛ پیش از واگذاری نقش مدیر، عضویت‌های قبلی را ممیزی کنید."
        )

    if user.groups.filter(pk=manager_group.pk).exists():
        return

    if not user.is_active:
        raise ValidationError("برای تعیین مدیر فروشگاه، حساب کاربری باید فعال باشد.")
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "تخصیص نقش مدیر باید داخل تراکنش اتمیک پنل مدیریت انجام شود."
        )

    Group.objects.select_for_update().get(pk=manager_group.pk)
    another_manager_exists = User.objects.filter(
        groups=manager_group,
        is_superuser=False,
    ).exclude(pk=user.pk).exists()
    if another_manager_exists:
        raise ValidationError(
            "مدیر فروشگاه دیگری از قبل تعیین شده است؛ ابتدا نقش او را لغو کنید."
        )


class StoreOwnerUserChangeForm(DjangoUserChangeForm):
    def clean(self):
        cleaned_data = super().clean()
        manager_group = Group.objects.filter(name=STORE_MANAGER_GROUP_NAME).first()
        selected_groups = cleaned_data.get("groups")
        manager_selected = bool(
            manager_group
            and selected_groups is not None
            and selected_groups.filter(pk=manager_group.pk).exists()
        )
        was_manager = bool(
            self.instance.pk
            and manager_group
            and self.instance.groups.filter(pk=manager_group.pk).exists()
        )

        if self.instance.is_superuser:
            if manager_selected:
                self.add_error(
                    "groups",
                    "صاحب فروشگاه نباید به‌عنوان مدیر فروشگاه تخصیص داده شود.",
                )
            return cleaned_data

        if manager_selected:
            try:
                ensure_store_manager_assignment_available(
                    self.instance,
                    manager_group,
                )
            except ValidationError as exc:
                self.add_error("groups", exc)
            cleaned_data["is_staff"] = True
        elif was_manager:
            cleaned_data["is_staff"] = False
        elif cleaned_data.get("is_staff"):
            self.add_error(
                "is_staff",
                "این حساب دسترسی پنل بدون نقش مدیر فروشگاه دارد؛ ابتدا دسترسی قدیمی آن را ممیزی کنید.",
            )

        return cleaned_data


@admin.register(Address)
class AddressAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    list_display = (
        "recipient_name",
        "user_email",
        "phone_number",
        "province",
        "city",
        "is_default",
        "updated",
    )
    list_filter = ("is_default", "province", "created")
    search_fields = (
        "recipient_name",
        "phone_number",
        "postal_code",
        "province",
        "city",
        "user__email",
        "user__username",
    )
    list_editable = ("is_default",)
    list_per_page = 25
    date_hierarchy = "updated"
    readonly_fields = ("created", "updated")
    list_select_related = ("user",)

    @admin.display(description="کاربر", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email or obj.user.get_username()

    # فیلدست برای دسته‌بندی بهتر در صفحه ویرایش تکی
    fieldsets = (
        ('اطلاعات کاربر و گیرنده', {
            'fields': ('user', 'recipient_name', 'phone_number')
        }),
        ('اطلاعات پستی', {
            'fields': ('province', 'city', 'address_line', 'postal_code')
        }),
        ('وضعیت', {
            'fields': ('is_default', 'created', 'updated')
        }),
    )

@admin.register(Profile)
class ProfileAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    # OTP values and their timestamps are intentionally excluded from the admin UI.
    list_display = ("user_display", "phone_number", "verification_badge", "created_at")
    list_filter = ("is_verified", "created_at")
    search_fields = ("phone_number", "user__username", "user__email")
    list_select_related = ("user",)
    list_per_page = 25
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    fields = ("user_display", "phone_number", "is_verified", "created_at", "updated_at")
    readonly_fields = fields

    @admin.display(description="مشتری", ordering="user__email")
    def user_display(self, obj):
        return obj.user.get_full_name() or obj.user.email or obj.user.get_username()

    @admin.display(description="وضعیت تأیید", ordering="is_verified")
    def verification_badge(self, obj):
        tone = "success" if obj.is_verified else "warning"
        label = "تأیید شده" if obj.is_verified else "تأیید نشده"
        return format_html(
            '<span class="store-status-pill store-status-pill--{}">{}</span>',
            tone,
            label,
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

class StoreOwnerOnlyAdminMixin:
    """Keep identity and permission administration exclusive to the store owner."""

    @staticmethod
    def _is_store_owner(request):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_superuser
        )

    def has_module_permission(self, request):
        return self._is_store_owner(request)

    def has_view_permission(self, request, obj=None):
        return self._is_store_owner(request)

    def has_add_permission(self, request):
        return self._is_store_owner(request)

    def has_change_permission(self, request, obj=None):
        return self._is_store_owner(request)

    def has_delete_permission(self, request, obj=None):
        return self._is_store_owner(request)


class StoreOwnerUserAdmin(StoreOwnerOnlyAdminMixin, DjangoUserAdmin):
    form = StoreOwnerUserChangeForm

    # Hide Django's raw permission editor and expose one predefined store role.
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("اطلاعات شخصی", {"fields": ("first_name", "last_name", "email")}),
        ("دسترسی پنل فروشگاه", {"fields": ("is_active", "is_staff", "groups")}),
        ("زمان‌های مهم", {"fields": ("last_login", "date_joined")}),
    )
    filter_horizontal = ("groups",)
    readonly_fields = ("last_login", "date_joined")
    list_filter = ("is_active", "is_staff")

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "groups":
            kwargs["queryset"] = Group.objects.filter(name=STORE_MANAGER_GROUP_NAME)
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        if field and db_field.name == "groups":
            field.label = "نقش فروشگاه"
            field.help_text = (
                "تنها نقش قابل تخصیص، گروه ثابت «مدیر فروشگاه» است. "
                "عضویت‌های قدیمی در گروه‌های دیگر هنگام ویرایش این حساب حفظ می‌شوند."
            )
        return field

    def formfield_for_booleanfield(self, db_field, request, **kwargs):
        field = super().formfield_for_booleanfield(db_field, request, **kwargs)
        if field and db_field.name == "is_staff":
            field.label = "دسترسی به پنل مدیریت"
            field.help_text = (
                "با نقش ثابت «مدیر فروشگاه» همگام است؛ دسترسی مستقل staff برای "
                "حساب‌های غیرمالک پذیرفته نمی‌شود."
            )
        return field

    def save_related(self, request, form, formsets, change):
        # The fixed-role UI must not silently strip historical group memberships.
        preserved_group_ids = []
        if change and form.instance.pk:
            preserved_group_ids = list(
                form.instance.groups.exclude(name=STORE_MANAGER_GROUP_NAME)
                .values_list("pk", flat=True)
            )
        super().save_related(request, form, formsets, change)
        if preserved_group_ids:
            form.instance.groups.add(*preserved_group_ids)


# The Group editor is intentionally removed. Owners assign the one predefined
# role from the user's "نقش فروشگاه" field instead of editing Django permissions.
if admin.site.is_registered(User):
    admin.site.unregister(User)
admin.site.register(User, StoreOwnerUserAdmin)

if admin.site.is_registered(Group):
    admin.site.unregister(Group)

