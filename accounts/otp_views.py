# accounts/views/otp_views.py
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth import login, get_user_model
from django.db import transaction
from accounts.services.otp import OTPService
from accounts.models.profile import Profile
from cart import services

User = get_user_model()


def _parse_json_request(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


@require_POST
def send_otp_view(request):
    """AJAX Endpoint for sending/resending OTP."""
    data = _parse_json_request(request)
    phone = data.get("phone", "")

    success, message, cooldown = OTPService.send_otp(phone)

    return JsonResponse(
        {"success": success, "message": message, "cooldown": cooldown},
        status=200 if success else 400,
    )


@require_POST
def verify_otp_view(request):
    """AJAX Endpoint for verifying OTP, auto-signup, and cart merging."""
    data = _parse_json_request(request)
    phone = OTPService.normalize_phone(data.get("phone", ""))
    code = data.get("code", "")

    success, message = OTPService.verify_otp(phone, code)
    if not success:
        return JsonResponse({"success": False, "message": message}, status=400)

    # 1. Fetch or Auto-Register User & Profile atomically
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
                # Create User with phone as username
                username = f"user_{phone}"
                user, _ = User.objects.get_or_create(username=username)
                # Create Profile
                Profile.objects.create(user=user, phone_number=phone, is_verified=True)
    except Exception as e:
        return JsonResponse(
            {
                "success": False,
                "message": "خطایی در پردازش اطلاعات کاربری رخ داد. لطفاً مجدداً تلاش کنید.",
            },
            status=500,
        )

    # 2. Login User to Django Session
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # 3. Merge Guest Cart (if any)
    try:
        cart_service = CartService(request)
        cart_service.merge_guest_cart_on_login(user)
    except Exception:
        # Prevent login failure if cart merge hits a non-critical edge-case
        pass

    return JsonResponse(
        {
            "success": True,
            "message": "ورود با موفقیت انجام شد.",
            "redirect_url": request.POST.get("next") or request.GET.get("next") or "/",
        }
    )
