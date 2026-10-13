from django.http import HttpResponse
from django.urls import reverse

CANONICAL_HOST = "codeamoz.com"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"


def robots_txt(request):
    sitemap_url = f"{CANONICAL_ORIGIN}{reverse('sitemap')}"

    content = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
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
