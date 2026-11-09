from catalog.models.category import Category
from catalog.models.storefront_settings import StorefrontSettings


def storefront_categories(request):
    """Expose active categories to storefront templates without querying on admin pages."""
    if request.path.startswith("/admin/"):
        return {}
    return {
        "categories": Category.objects.filter(is_active=True),
        "storefront_settings": StorefrontSettings.objects.filter(
            pk=StorefrontSettings.SINGLETON_PK
        ).first(),
    }
