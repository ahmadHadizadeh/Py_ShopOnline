from catalog.models.category import Category
from catalog.models.storefront_settings import StorefrontSettings


def storefront_categories(request):
    """Expose active categories to storefront templates without querying on admin pages."""
    if request.path.startswith("/admin/"):
        return {}
    storefront_settings = StorefrontSettings.objects.filter(
        pk=StorefrontSettings.SINGLETON_PK
    ).first()
    return {
        "categories": Category.objects.filter(is_active=True),
        "storefront_settings": storefront_settings,
        "storefront_hero_title_line_1": (
            storefront_settings.hero_title_line_1
            if storefront_settings
            else StorefrontSettings.DEFAULT_HERO_TITLE_LINE_1
        ),
        "storefront_hero_title_line_2": (
            storefront_settings.hero_title_line_2
            if storefront_settings
            else StorefrontSettings.DEFAULT_HERO_TITLE_LINE_2
        ),
    }
