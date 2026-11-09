from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template


register = template.Library()


@register.filter
def format_price(value):
    """Format a storefront price with grouping, without converting through float."""
    if value is None:
        return "—"

    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return str(value)
        rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    return f"{rounded:,.0f}"
