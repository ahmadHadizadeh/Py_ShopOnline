# accounts/otp_views.py
import json
import logging
from importlib import import_module

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.http import JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import Profile
from accounts.services.otp import SMSIRService
from cart.services import merge_guest_cart_to_user

logger = logging.getLogger(__name__)
User = get_user_model()


def parse_json_request(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    return data


def get_safe_login_redirect(request, next_url):
    """Return a safe same-site redirect target or the site root."""
    next_url = (next_url or "").strip()
    if not next_url:
        return "/"

    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return "/"


@require_POST
def send_otp_view(request):
    data = parse_json_request(request)
    if data is None:
        return JsonResponse(
            {
                "status": "error",
                "message": "اطلاعات درخواست نامعتبر است.",
            },
            status=400,
        )

    # پشتیبانی از هر دو نامی که در کد فعلی پروژه استفاده شده‌اند.
    raw_phone = data.get("phone") or data.get("mobile") or ""
    phone = SMSIRService.normalize_phone(str(raw_phone))

    if not phone:
        return JsonResponse(
            {
                "status": "error",
                "message": "شماره موبایل وارد شده معتبر نیست. مثال: 09123456789",
            },
            status=400,
        )

    try:
        success, message, ttl = SMSIRService.send_otp(phone)
    except Exception:
        logger.exception("[Send OTP] Unexpected error")
        return JsonResponse(
            {
                "status": "error",
                "message": "خطای سیستمی رخ داده است. لطفاً دوباره تلاش کنید.",
            },
            status=500,
        )

    if not success:
        return JsonResponse(
            {
                "status": "error",
                "message": message,
                "ttl": ttl,
            },
            status=400,
        )

    return JsonResponse(
        {
            "status": "success",
            "message": message,
            "ttl": ttl,
        }
    )


def _restore_login_request_state(
    request,
    *,
    session_key,
    session_data,
    session_modified,
    session_accessed,
    original_user,
    original_csrf_cookie,
):
    """
    Restore in-memory request state after the surrounding DB transaction rolls
    back a failed login. The database-backed session row itself is restored by
    that transaction; this replaces the SessionStore whose key may have been
    rotated by django.contrib.auth.login().
    """
    session_module = import_module(settings.SESSION_ENGINE)
    restored_session = session_module.SessionStore(session_key=session_key)

    # Restore the in-memory snapshot without querying the database. The outer
    # transaction has already rolled back the original session row, and the
    # database connection may itself be the reason the login transaction failed.
    restored_session._session_cache = dict(session_data)
    restored_session.modified = session_modified
    restored_session.accessed = session_accessed

    request.session = restored_session
    request.user = original_user

    if original_csrf_cookie is None:
        request.META.pop("CSRF_COOKIE", None)
    else:
        request.META["CSRF_COOKIE"] = original_csrf_cookie


@require_POST
def verify_otp_view(request):
    data = parse_json_request(request)
    if data is None:
        return JsonResponse(
            {
                "status": "error",
                "message": "اطلاعات درخواست نامعتبر است.",
            },
            status=400,
        )

    raw_phone = data.get("phone") or data.get("mobile") or ""
    phone = SMSIRService.normalize_phone(str(raw_phone))
    code = str(data.get("code", "")).strip()
    next_url = get_safe_login_redirect(request, data.get("next"))

    if not phone:
        return JsonResponse(
            {
                "status": "error",
                "message": "شماره موبایل وارد شده معتبر نیست.",
            },
            status=400,
        )

    if not code:
        return JsonResponse(
            {
                "status": "error",
                "message": "کد تأیید الزامی است.",
            },
            status=400,
        )

    # Capture the guest session before login() rotates its key. If any later
    # operation fails, the outer transaction restores its database row and we
    # also restore the in-memory request state for SessionMiddleware.
    original_session_key = request.session.session_key
    original_session_data = dict(request.session.items())
    original_session_modified = request.session.modified
    original_session_accessed = request.session.accessed
    original_user = request.user
    original_csrf_cookie = request.META.get("CSRF_COOKIE")

    try:
        with transaction.atomic():
            # SMSIRService.verify_otp() uses a nested atomic block and a
            # PostgreSQL transaction-level advisory lock. Because this view
            # owns the outer transaction, the lock and DatabaseCache mutation
            # remain uncommitted until profile, cart, and session work succeeds.
            is_valid, message = SMSIRService.verify_otp(phone, code)

            if not is_valid:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": message,
                    },
                    status=400,
                )

            profile = (
                Profile.objects.select_related("user")
                .filter(phone_number=phone)
                .first()
            )

            if profile is not None:
                user = profile.user
            else:
                user = User.objects.filter(username=phone).first()

                if user is None:
                    # The post_save signal creates the Profile in this same
                    # transaction. Keep that existing project behavior.
                    user = User.objects.create_user(username=phone)

                profile, _ = Profile.objects.get_or_create(user=user)

            if not user.is_active:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "این حساب کاربری غیرفعال است؛ برای پیگیری با پشتیبانی تماس بگیرید.",
                    },
                    status=403,
                )

            profile.phone_number = phone
            profile.is_verified = True
            profile.save(
                update_fields=["phone_number", "is_verified", "updated_at"]
            )

            merge_guest_cart_to_user(
                request,
                user=user,
                guest_session_key=original_session_key,
            )

            login(request, user)

            # login() rotates/creates the session key but SessionMiddleware
            # normally persists the modified session after the view returns.
            # Save it now so the session row participates in this transaction.
            request.session.save()

    except Exception:
        try:
            _restore_login_request_state(
                request,
                session_key=original_session_key,
                session_data=original_session_data,
                session_modified=original_session_modified,
                session_accessed=original_session_accessed,
                original_user=original_user,
                original_csrf_cookie=original_csrf_cookie,
            )
        except Exception:
            logger.exception("[Verify OTP] Failed to restore request session after rollback")

        logger.exception("[Verify OTP] Atomic login operation failed")
        return JsonResponse(
            {
                "status": "error",
                "message": "خطا در ورود کاربر.",
            },
            status=500,
        )

    return JsonResponse(
        {
            "status": "success",
            "message": "ورود با موفقیت انجام شد.",
            "redirect_url": next_url,
        }
    )
