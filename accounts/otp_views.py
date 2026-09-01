# accounts/views/otp_views.py

import json
import logging
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import Profile
from accounts.services.otp import OTPService
from cart.services import merge_guest_cart_to_user

logger = logging.getLogger(__name__)
User = get_user_model()


def _parse_json_request(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


@require_POST
def send_otp_view(request):
    """ایمیل/پیامک کد تایید ورود برای فرانت‌اند (AJAX Endpoint)"""
    data = _parse_json_request(request)
    phone = data.get("phone", "")

    success, message, cooldown = OTPService.send_otp(phone)

    return JsonResponse(
        {"success": success, "message": message, "cooldown": cooldown},
        status=200 if success else 400,
    )


@require_POST
def verify_otp_view(request):
    """اعتبارسنجی کد، ثبت‌نام خودکار، لاگین سشن و ادغام سبد خرید مهمان"""
    data = _parse_json_request(request)
    phone = OTPService.normalize_phone(data.get("phone", ""))
    code = data.get("code", "")

    success, message = OTPService.verify_otp(phone, code)
    if not success:
        return JsonResponse({"success": False, "message": message}, status=400)

    # ۱. ثبت نام خودکار یا بازیابی کاربر و پروفایل با ایمنی بالا در برابر Race Condition
    try:
        with transaction.atomic():
            profile = (
                Profile.objects.select_related("user")
                .filter(phone_number=phone)
                .first()
            )
            if profile:
                user = profile.user
                if not profile.is_verified:
                    profile.is_verified = True
                    profile.save(update_fields=["is_verified"])
            else:
                username = f"user_{phone}"
                user, _ = User.objects.get_or_create(username=username)
                profile, created = Profile.objects.get_or_create(
                    user=user,
                    defaults={"phone_number": phone, "is_verified": True},
                )
                if not created and profile.phone_number != phone:
                    profile.phone_number = phone
                    profile.is_verified = True
                    profile.save(update_fields=["phone_number", "is_verified"])
    except Exception as exc:
        logger.exception(f"User registration/login error for {phone}: {exc}")
        return JsonResponse(
            {
                "success": False,
                "message": "خطایی در ثبت اطلاعات کاربری رخ داد. لطفاً مجدداً تلاش کنید.",
            },
            status=500,
        )

    # ۲. لاگین کاربر در سشن جنگو
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # ۳. ادغام اتمیک سبد خرید مهمان با کاربر
    try:
        merge_guest_cart_to_user(request)
    except Exception as cart_err:
        logger.exception(
            f"Non-critical: Cart merge failed for user {user.id}: {cart_err}"
        )

    # ۴. تعیین مسیر ریدایرکت نهایی
    redirect_url = (
        data.get("next") or request.POST.get("next") or request.GET.get("next") or "/"
    )

    return JsonResponse(
        {
            "success": True,
            "message": "ورود با موفقیت انجام شد.",
            "redirect_url": redirect_url,
        }
    )
