# accounts/otp_views.py
import json
import logging

from django.contrib.auth import get_user_model, login
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import Profile
from accounts.services.otp import SMSIRService

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
        logger.exception("[Send OTP] Unexpected error for phone=%s", phone)
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

    is_valid, message = SMSIRService.verify_otp(phone, code)
    if not is_valid:
        return JsonResponse(
            {
                "status": "error",
                "message": message,
            },
            status=400,
        )

    try:
        profile = (
            Profile.objects.select_related("user").filter(phone_number=phone).first()
        )

        if profile is not None:
            user = profile.user
        else:
            user = User.objects.filter(username=phone).first()

            if user is None:
                user = User.objects.create_user(username=phone)

            profile, _ = Profile.objects.get_or_create(user=user)

            if profile.phone_number != phone:
                profile.phone_number = phone
                profile.save(update_fields=["phone_number"])

        login(request, user)

    except Exception:
        logger.exception("[Verify OTP] Login error for phone=%s", phone)
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
        }
    )
