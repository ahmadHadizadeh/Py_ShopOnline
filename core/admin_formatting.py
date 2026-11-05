"""Shared, presentation-only formatting for Django admin changelists.

The helpers never mutate model fields or query the database. List-display
formatters share a cache created for the current changelist request.
"""
import calendar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

import jdatetime
from django.contrib import admin
from django.contrib.admin.templatetags.admin_list import date_hierarchy
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.http import QueryDict
from django.utils import timezone


JALALI_MONTH_NAMES = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_NO_VALUE = "—"


def _persian_digits(value):
    return str(value).translate(_PERSIAN_DIGITS)


def _cached(cache, key, factory):
    if cache is None:
        return factory()
    try:
        if key not in cache:
            cache[key] = factory()
        return cache[key]
    except TypeError:
        # A value that cannot be used as a dictionary key is still formatted
        # correctly; it simply skips caching.
        return factory()


def _owner_cache(owner):
    if owner is None:
        return None
    cache = getattr(owner, "_admin_display_format_cache", None)
    if cache is None:
        cache = {}
        setattr(owner, "_admin_display_format_cache", cache)
    return cache


def format_jalali_datetime(value, *, cache=None, owner=None):
    """Format a date/datetime in the Jalali calendar, preserving local time."""
    if value is None:
        return _NO_VALUE
    if cache is None:
        cache = _owner_cache(owner)

    def build():
        if isinstance(value, datetime):
            local_value = (
                timezone.localtime(value) if timezone.is_aware(value) else value
            )
            jalali = jdatetime.datetime.fromgregorian(
                datetime=local_value,
                locale=jdatetime.FA_LOCALE,
            )
            return (
                f"{_persian_digits(jalali.day)} "
                f"{JALALI_MONTH_NAMES[jalali.month - 1]} "
                f"{_persian_digits(jalali.year)}، ساعت "
                f"{_persian_digits(f'{local_value.hour:02d}:{local_value.minute:02d}')}"
            )

        if isinstance(value, date):
            jalali = jdatetime.date.fromgregorian(
                date=value,
                locale=jdatetime.FA_LOCALE,
            )
            return (
                f"{_persian_digits(jalali.day)} "
                f"{JALALI_MONTH_NAMES[jalali.month - 1]} "
                f"{_persian_digits(jalali.year)}"
            )

        return str(value)

    return _cached(cache, ("jalali-date", value), build)


def format_toman(value, *, cache=None, owner=None):
    """Format a toman amount with grouping and no visible fractional digits."""
    if value is None:
        return _NO_VALUE
    if cache is None:
        cache = _owner_cache(owner)

    def build():
        try:
            amount = Decimal(str(value))
            if not amount.is_finite():
                return str(value)
            rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            return str(value)
        return f"{rounded:,.0f} تومان"

    return _cached(cache, ("toman", str(value)), build)


def _jalali_date(value, cache):
    # Date hierarchy values are Gregorian dates supplied by Django's own
    # date_hierarchy implementation. Keep their query parameters untouched.
    return jdatetime.date.fromgregorian(
        date=value,
        locale=jdatetime.FA_LOCALE,
    )


def _jalali_period_label(year, month=None, day=None, *, cache=None):
    year = int(year)

    if month is not None:
        month = int(month)
        if day is not None:
            jalali = _cached(
                cache,
                ("gregorian-to-jalali", date(year, month, int(day))),
                lambda: _jalali_date(date(year, month, int(day)), cache),
            )
            return (
                f"{_persian_digits(jalali.day)} "
                f"{JALALI_MONTH_NAMES[jalali.month - 1]} "
                f"{_persian_digits(jalali.year)}"
            )

        jalali_months = []
        days_in_month = calendar.monthrange(year, month)[1]
        for gregorian_day in range(1, days_in_month + 1):
            gregorian_date = date(year, month, gregorian_day)
            jalali = _cached(
                cache,
                ("gregorian-to-jalali", gregorian_date),
                lambda gregorian_date=gregorian_date: _jalali_date(
                    gregorian_date, cache
                ),
            )
            item = (jalali.year, jalali.month)
            if item not in jalali_months:
                jalali_months.append(item)

        years = {jalali_year for jalali_year, _ in jalali_months}
        if len(years) == 1:
            month_names = " و ".join(
                JALALI_MONTH_NAMES[jalali_month - 1]
                for _, jalali_month in jalali_months
            )
            return f"{month_names} {_persian_digits(jalali_months[0][0])}"

        return " و ".join(
            f"{JALALI_MONTH_NAMES[jalali_month - 1]} "
            f"{_persian_digits(jalali_year)}"
            for jalali_year, jalali_month in jalali_months
        )

    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    jalali_years = {
        _cached(
            cache,
            ("gregorian-to-jalali", gregorian_day),
            lambda gregorian_day=gregorian_day: _jalali_date(gregorian_day, cache),
        ).year
        for gregorian_day in (first_day, last_day)
    }
    return "–".join(_persian_digits(item) for item in sorted(jalali_years))


def _query_params_for_hierarchy(link, fallback_params):
    query = QueryDict(urlsplit(link or "").query)
    if query:
        return query

    query = QueryDict("", mutable=True)
    for key, value in (fallback_params or {}).items():
        if isinstance(value, (list, tuple)):
            query.setlist(key, [str(item) for item in value])
        elif value is not None:
            query[key] = str(value)
    return query


def jalali_date_hierarchy_label(
    link,
    *,
    date_field,
    fallback_params=None,
    cache=None,
):
    """Convert only date-hierarchy link labels, never their Gregorian filters."""
    params = _query_params_for_hierarchy(link, fallback_params)
    prefix = f"{date_field}__"
    year = params.get(f"{prefix}year")
    if not year:
        return "همه تاریخ‌ها"

    return _jalali_period_label(
        year,
        params.get(f"{prefix}month"),
        params.get(f"{prefix}day"),
        cache=cache,
    )


class AdminDisplayFormattingMixin:
    """Apply shared Persian date and explicit money formatting to changelists."""

    change_list_template = "admin/persian_change_list.html"
    money_display_fields = frozenset()
    date_display_fields = {}

    def get_list_display(self, request):
        fields = list(super().get_list_display(request))
        request_cache = {}

        formatted_fields = []
        for field_name in fields:
            if not isinstance(field_name, str):
                formatted_fields.append(field_name)
                continue

            money_source = (
                self.money_display_fields.get(field_name)
                if isinstance(self.money_display_fields, dict)
                else field_name if field_name in self.money_display_fields else None
            )
            if money_source is not None:
                formatted_fields.append(
                    self._make_admin_display(
                        field_name,
                        money_source,
                        "money",
                        request_cache,
                    )
                )
                continue

            date_source = self.date_display_fields.get(field_name)
            if date_source is None:
                try:
                    model_field = self.model._meta.get_field(field_name)
                except (FieldDoesNotExist, LookupError, AttributeError):
                    model_field = None
                if isinstance(model_field, (models.DateField, models.DateTimeField)):
                    date_source = field_name

            if date_source is not None:
                formatted_fields.append(
                    self._make_admin_display(
                        field_name,
                        date_source,
                        "date",
                        request_cache,
                    )
                )
            else:
                formatted_fields.append(field_name)

        return formatted_fields

    def _make_admin_display(self, display_name, source_name, kind, cache):
        source_parts = source_name.split("__")

        def display_value(obj):
            value = obj
            for part in source_parts:
                value = getattr(value, part, None)
                if value is None:
                    break
            if kind == "money":
                return format_toman(value, cache=cache)
            return format_jalali_datetime(value, cache=cache)

        display_value.__name__ = f"_admin_{kind}_{display_name}"
        original = getattr(self, display_name, None)
        description = getattr(original, "short_description", None)
        if description is None:
            try:
                description = self.model._meta.get_field(source_name).verbose_name
            except (FieldDoesNotExist, LookupError, AttributeError):
                description = display_name.replace("_", " ").capitalize()

        return admin.display(
            description=description,
            ordering=source_name,
        )(display_value)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(
            request,
            extra_context=extra_context,
        )
        context = getattr(response, "context_data", None)
        change_list = context.get("cl") if context is not None else None

        if change_list is not None and change_list.date_hierarchy:
            # Compute Django's normal hierarchy once. The custom template below
            # replaces the stock tag, so this does not add a duplicate query.
            hierarchy_cache = {}
            hierarchy = date_hierarchy(change_list)
            if hierarchy:
                field_name = change_list.date_hierarchy
                if hierarchy.get("back"):
                    back = hierarchy["back"]
                    back["title"] = jalali_date_hierarchy_label(
                        back.get("link"),
                        date_field=field_name,
                        fallback_params=change_list.params,
                        cache=hierarchy_cache,
                    )
                for choice in hierarchy.get("choices", ()):
                    choice["title"] = jalali_date_hierarchy_label(
                        choice.get("link"),
                        date_field=field_name,
                        fallback_params=change_list.params,
                        cache=hierarchy_cache,
                    )
            context["admin_date_hierarchy"] = hierarchy

        return response
