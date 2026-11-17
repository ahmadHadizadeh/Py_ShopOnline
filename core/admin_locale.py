from django.conf import settings
from django.utils import translation


class AdminLocaleMiddleware:
    """Use Persian for Django Admin without changing the public storefront locale."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        admin_path = f"/{settings.ADMIN_URL_PATH}"
        if path == admin_path or path.startswith(f"{admin_path}/"):
            with translation.override("fa-ir"):
                return self.get_response(request)
        return self.get_response(request)
