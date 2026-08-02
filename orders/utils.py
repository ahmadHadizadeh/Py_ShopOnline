# orders/utils.py

from django.db.models import Sum
from cart.models import Cart
from accounts.models import Address



def calculate_shipping_cost(address: Address) -> float:
    """
    Calculates shipping cost based on the provided address.
    TODO: Implement actual shipping cost logic.
    Placeholder returns a fixed value.
    """
    # Placeholder value: 50,000 Toman
    return 50000.0


def calculate_discount(cart: Cart) -> float:
    """
    Calculates applicable discounts for the given cart.
    TODO: Implement actual discount logic (e.g., coupons, promotions).
    Placeholder returns zero discount.
    """
    # Placeholder: No discount applied for now
    return 0.0


def calculate_order_totals(cart: Cart, shipping_address: Address) -> dict:
    """
    Calculates subtotal, discount, shipping cost, and the final amount for an order
    based on the current cart items and shipping address.
    """
    # Calculate subtotal from cart items
    # Assumes item.variant.price or item.product.price exists and is valid
    subtotal = sum(
        item.quantity * (item.variant.price if item.variant else item.product.price)
        for item in cart.items.all()
    )

    discount = calculate_discount(cart)
    shipping = calculate_shipping_cost(shipping_address)

    # Calculate final total: subtotal - discount + shipping
    final_total = subtotal - discount + shipping

    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "final_amount": final_total,
    }
