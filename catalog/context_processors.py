from catalog.models.category import Category


def storefront_categories(request):
    """Expose active categories to all storefront templates."""
    return {"categories": Category.objects.filter(is_active=True)}
