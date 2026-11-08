from catalog.models.category import Category


def storefront_categories(request):
    """Expose active categories to storefront templates without querying on admin pages."""
    if request.path.startswith("/admin/"):
        return {}
    return {"categories": Category.objects.filter(is_active=True)}
