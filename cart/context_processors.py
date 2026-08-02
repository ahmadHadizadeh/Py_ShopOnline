from cart.services import get_or_create_cart


def cart_summary(request):
    if not hasattr(request, "session"):
        return {"cart_total_items": 0}

    try:
        cart = get_or_create_cart(request)
        cart = cart.__class__.objects.prefetch_related("items").get(pk=cart.pk)
        return {"cart_total_items": cart.total_items}
    except Exception:
        return {"cart_total_items": 0}
