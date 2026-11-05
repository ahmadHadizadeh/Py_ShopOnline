from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import admin
from django.test import SimpleTestCase

from orders.admin import OrderAdmin, _jalali_period_label
from orders.models import Order


class OrderAdminPersianDisplayTests(SimpleTestCase):
    def setUp(self):
        self.model_admin = OrderAdmin(Order, admin.site)

    def test_created_timestamp_is_shown_as_tehran_jalali_date(self):
        order = SimpleNamespace(
            created=datetime(
                2026, 10, 14, 14, 11, tzinfo=datetime_timezone.utc
            )
        )

        self.assertEqual(
            self.model_admin.created_jalali_display(order),
            "۲۲ مهر ۱۴۰۵، ساعت ۱۷:۴۱",
        )

    def test_final_amount_uses_three_digit_grouping_without_decimals(self):
        order = SimpleNamespace(final_amount=Decimal("489000000.00"))

        self.assertEqual(
            self.model_admin.get_final_amount_display(order),
            "489,000,000 تومان",
        )

    def test_gregorian_hierarchy_scopes_are_labeled_with_matching_jalali_dates(self):
        self.assertEqual(
            _jalali_period_label(2026, 10, 14),
            "۲۲ مهر ۱۴۰۵",
        )
        self.assertEqual(
            _jalali_period_label(2026, 10),
            "مهر و آبان ۱۴۰۵",
        )
        self.assertEqual(
            _jalali_period_label(2026),
            "۱۴۰۴–۱۴۰۵",
        )
