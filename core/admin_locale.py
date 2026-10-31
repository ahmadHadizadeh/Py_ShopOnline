from django.utils import translation


class AdminLocaleMiddleware:
    """Use Persian for Django Admin without changing the public storefront locale."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path == "/admin" or path.startswith("/admin/"):
            with translation.override("fa-ir"):
                return self.get_response(request)
        return self.get_response(request)
