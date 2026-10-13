from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from core.seo import CANONICAL_HOST

from catalog.models.category import Category
from catalog.models.product import Product


class CanonicalSitemap(Sitemap):
    def get_domain(self, site=None):
        return CANONICAL_HOST


class StaticSitemap(CanonicalSitemap):
    protocol = "https"
    changefreq = "weekly"
    priority = 1.0

    def items(self):
        return ["catalog:product_list"]

    def location(self, item):
        return reverse(item)


class CategorySitemap(CanonicalSitemap):
    protocol = "https"
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Category.objects.filter(is_active=True).order_by("slug")


class ProductSitemap(CanonicalSitemap):
    protocol = "https"
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return (
            Product.objects.filter(is_active=True)
            .select_related("category")
            .order_by("slug")
        )

    def lastmod(self, item):
        return item.updated


sitemaps = {
    "static": StaticSitemap,
    "categories": CategorySitemap,
    "products": ProductSitemap,
}
