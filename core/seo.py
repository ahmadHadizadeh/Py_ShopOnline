from django.conf import settings
from django.http import Http404, HttpResponse
from django.urls import reverse
CANONICAL_HOST = "codeamoz.com"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"


def sitemap_view(request):
    if settings.DEMO_MODE:
        raise Http404
    from django.contrib.sitemaps.views import sitemap
    from catalog.sitemaps import sitemaps

    return sitemap(request, sitemaps=sitemaps)


def robots_txt(request):
    if settings.DEMO_MODE:
        content = "User-agent: *\nDisallow: /\n"
    else:
        sitemap_url = f"{CANONICAL_ORIGIN}{reverse('sitemap')}"
        content = "\n".join([
            "User-agent: *",
            "Allow: /",
            "Disallow: /cart/",
            "Disallow: /accounts/",
            "Disallow: /orders/",
            f"Sitemap: {sitemap_url}",
            "",
        ])

    return HttpResponse(
        content,
        content_type="text/plain; charset=utf-8",
    )
