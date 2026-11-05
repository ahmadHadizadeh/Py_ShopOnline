from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import admin
from django.test import SimpleTestCase

from core.admin_formatting import (
    AdminDisplayFormattingMixin,
    format_jalali_datetime,
    format_toman,
    jalali_date_hierarchy_label,
)
from orders.models import Order


class FormattingSampleAdmin(AdminDisplayFormattingMixin, admin.ModelAdmin):
    list_display = ("created", "final_amount", "stock")
    money_display_fields = {"final_amount": "final_amount"}


class AdminPersianFormattingTests(SimpleTestCase):
    def test_datetime_uses_jalali_date_and_preserves_existing_time(self):
        value = datetime(
            2026, 10, 14, 14, 11, tzinfo=datetime_timezone.utc
        )

        self.assertEqual(
            format_jalali_datetime(value),
            "۲۲ مهر ۱۴۰۵، ساعت ۱۴:۱۱",
        )

    def test_date_field_uses_jalali_calendar_without_time(self):
        self.assertEqual(
            format_jalali_datetime(date(2026, 10, 14)),
            "۲۲ مهر ۱۴۰۵",
        )

    def test_toman_amounts_are_grouped_and_rounded_without_decimal_places(self):
        self.assertEqual(
            format_toman(Decimal("489000000.00")),
            "489,000,000 تومان",
        )
        self.assertEqual(
            format_toman(Decimal("489000000.50")),
            "489,000,001 تومان",
        )
        self.assertEqual(format_toman(None), "—")

    def test_date_hierarchy_supports_different_field_names_and_preserves_gregorian_scope(self):
        self.assertEqual(
            jalali_date_hierarchy_label(
                "?created__year=2026&created__month=10&created__day=14",
                date_field="created",
            ),
            "۲۲ مهر ۱۴۰۵",
        )
        self.assertEqual(
            jalali_date_hierarchy_label(
                "?created_at__year=2026&created_at__month=10",
                date_field="created_at",
            ),
            "مهر و آبان ۱۴۰۵",
        )
        self.assertEqual(
            jalali_date_hierarchy_label(
                "?status=paid",
                date_field="created",
            ),
            "همه تاریخ‌ها",
        )

    def test_gregorian_year_hierarchy_can_span_two_jalali_years(self):
        self.assertEqual(
            jalali_date_hierarchy_label(
                "?created__year=2026",
                date_field="created",
            ),
            "۱۴۰۴–۱۴۰۵",
        )

    def test_changelist_mixin_wraps_only_explicit_money_and_date_columns(self):
        model_admin = FormattingSampleAdmin(Order, admin.site)
        columns = model_admin.get_list_display(SimpleNamespace())

        created_column, amount_column, stock_column = columns
        row = SimpleNamespace(
            created=datetime(
                2026, 10, 14, 14, 11, tzinfo=datetime_timezone.utc
            ),
            final_amount=Decimal("489000000.00"),
            stock=123456,
        )

        self.assertTrue(callable(created_column))
        self.assertTrue(callable(amount_column))
        self.assertEqual(created_column(row), "۲۲ مهر ۱۴۰۵، ساعت ۱۴:۱۱")
        self.assertEqual(amount_column(row), "489,000,000 تومان")
        self.assertEqual(stock_column, "stock")
        self.assertEqual(created_column.admin_order_field, "created")
        self.assertEqual(amount_column.admin_order_field, "final_amount")

    def test_central_mixin_is_attached_to_every_affected_changelist(self):
        from accounts.admin import AddressAdmin, ProfileAdmin
        from cart.admin import CartAdmin, CartItemAdmin
        from catalog.admin import (
            InventoryMovementAdmin,
            ProductAdmin,
            ProductVariantAdmin,
            ReviewAdmin,
        )
        from orders.admin import (
            OrderAddressSnapshotAdmin,
            OrderAdmin,
            OrderItemAdmin,
            PaymentAdmin,
            ShippingMethodAdmin,
        )

        affected_admins = (
            AddressAdmin,
            ProfileAdmin,
            CartAdmin,
            CartItemAdmin,
            ProductAdmin,
            InventoryMovementAdmin,
            ProductVariantAdmin,
            ReviewAdmin,
            OrderAdmin,
            OrderItemAdmin,
            PaymentAdmin,
            OrderAddressSnapshotAdmin,
            ShippingMethodAdmin,
        )
        self.assertTrue(
            all(issubclass(model_admin, AdminDisplayFormattingMixin)
                for model_admin in affected_admins)
        )
