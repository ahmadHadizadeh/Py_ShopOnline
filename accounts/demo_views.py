import secrets

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from cart.services import merge_guest_cart_to_user
from core.demo_security import DEMO_CUSTOMER_USERNAME_PREFIX


User = get_user_model()
DEMO_SESSION_USERNAME_KEY = "demo_customer_username"
DEMO_SESSION_MAX_AGE = 24 * 60 * 60


def _safe_demo_redirect(request, candidate):
    candidate = (candidate or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return "/"


@require_POST
def demo_access_view(request):
    """Create or resume a private, session-bound demo customer account."""
    if not settings.DEMO_MODE:
        raise Http404

    next_url = _safe_demo_redirect(request, request.POST.get("next"))

    # Never silently replace an already authenticated identity (especially
    # the separate demo manager account) with a storefront demo identity.
    if request.user.is_authenticated:
        return redirect(next_url)

    if request.session.session_key is None:
        request.session.create()
    guest_session_key = request.session.session_key

    username = request.session.get(DEMO_SESSION_USERNAME_KEY, "")
    user = None
    if (
        isinstance(username, str)
        and username.startswith(DEMO_CUSTOMER_USERNAME_PREFIX)
    ):
        user = User.objects.filter(
            username=username,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        ).first()

    with transaction.atomic():
        if user is None:
            username = (
                f"{DEMO_CUSTOMER_USERNAME_PREFIX}{secrets.token_urlsafe(18)}"
            )
            user = User.objects.create_user(username=username)

        merge_guest_cart_to_user(
            request,
            user=user,
            guest_session_key=guest_session_key,
        )

        request.session[DEMO_SESSION_USERNAME_KEY] = user.get_username()
        request.session.set_expiry(DEMO_SESSION_MAX_AGE)
        login(
            request,
            user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        request.session.save()

    return redirect(next_url)
