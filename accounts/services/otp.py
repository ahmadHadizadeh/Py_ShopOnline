# accounts/services/otp.py
import re
import secrets
import hashlib
from typing import Tuple, Optional
from django.core.cache import cache
from django.conf import settings


class OTPService:
    """
    Production-Ready OTP Service with Hashing, Cooldown, Rate Limiting and Brute-force protection.
    """

    OTP_EXPIRY_SECONDS = 180  # 3 minutes lifetime
    COOLDOWN_SECONDS = 120  # 2 minutes wait before resend
    MAX_ATTEMPTS = 5  # Max invalid verification attempts before lock
    MAX_DAILY_REQUESTS = 10  # Max OTP requests per phone per day

    PHONE_REGEX = re.compile(r"^09\d{9}$")

    @classmethod
    def normalize_phone(cls, phone: str) -> str:
        """Sanitize and validate Iranian phone numbers."""
        if not phone:
            return ""
        phone = phone.strip()
        # Convert Persian/Arabic digits to English digits
        persian_digits = "۰۱۲۳۴۵۶۷۸۹"
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        for i in range(10):
            phone = phone.replace(persian_digits[i], str(i)).replace(
                arabic_digits[i], str(i)
            )
        return phone

    @classmethod
    def validate_phone(cls, phone: str) -> bool:
        return bool(cls.PHONE_REGEX.match(phone))

    @classmethod
    def _get_cache_keys(cls, phone: str) -> dict:
        return {
            "otp_hash": f"otp:hash:{phone}",
            "cooldown": f"otp:cooldown:{phone}",
            "attempts": f"otp:attempts:{phone}",
            "daily_count": f"otp:daily:{phone}",
        }

    @classmethod
    def _hash_code(cls, code: str, phone: str) -> str:
        secret_salt = getattr(settings, "SECRET_KEY", "default-salt")
        payload = f"{phone}:{code}:{secret_salt}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def send_otp(cls, raw_phone: str) -> Tuple[bool, str, int]:
        """
        Generates and prepares OTP for sending.
        Returns: (success: bool, message: str, remaining_cooldown: int)
        """
        phone = cls.normalize_phone(raw_phone)
        if not cls.validate_phone(phone):
            return False, "شماره موبایل وارد شده معتبر نمی‌باشد (مثال: 09123456789).", 0

        keys = cls._get_cache_keys(phone)

        # 1. Check Cooldown
        cooldown = cache.get(keys["cooldown"])
        if cooldown:
            return False, "لطفاً تا پایان زمان تعیین‌شده شکیبا باشید.", cooldown

        # 2. Check Daily Limit
        daily_count = cache.get(keys["daily_count"], 0)
        if daily_count >= cls.MAX_DAILY_REQUESTS:
            return False, "تعداد درخواست‌های پیامک برای امروز به سقف مجاز رسیده است.", 0

        # 3. Generate 5-digit secure OTP code
        code = str(secrets.randbelow(90000) + 10000)
        hashed_code = cls._hash_code(code, phone)

        # 4. Save to Cache
        cache.set(keys["otp_hash"], hashed_code, timeout=cls.OTP_EXPIRY_SECONDS)
        cache.set(keys["cooldown"], cls.COOLDOWN_SECONDS, timeout=cls.COOLDOWN_SECONDS)
        cache.set(keys["attempts"], 0, timeout=cls.OTP_EXPIRY_SECONDS)
        cache.set(keys["daily_count"], daily_count + 1, timeout=86400)  # 24 hours

        # 5. Dispatch SMS (Mock or Provider)
        cls._dispatch_sms(phone, code)

        return True, "کد تأیید با موفقیت ارسال شد.", cls.COOLDOWN_SECONDS

    @classmethod
    def verify_otp(cls, raw_phone: str, code: str) -> Tuple[bool, str]:
        """
        Verifies the user-submitted OTP with Brute-force protection.
        Returns: (success: bool, message: str)
        """
        phone = cls.normalize_phone(raw_phone)
        code = (code or "").strip()

        if not cls.validate_phone(phone) or not (code.isdigit() and len(code) == 5):
            return False, "کد تأیید یا شماره موبایل نامعتبر است."

        keys = cls._get_cache_keys(phone)
        stored_hash = cache.get(keys["otp_hash"])

        if not stored_hash:
            return False, "کد تأیید منقضی شده یا درخواستی ثبت نشده است."

        attempts = cache.get(keys["attempts"], 0)
        if attempts >= cls.MAX_ATTEMPTS:
            # Purge OTP on brute force
            cache.delete(keys["otp_hash"])
            cache.delete(keys["attempts"])
            return (
                False,
                "تعداد تلاش‌های ناموفق بیش از حد مجاز بود. لطفاً کد جدید دریافت کنید.",
            )

        submitted_hash = cls._hash_code(code, phone)

        # Constant-time comparison to protect against timing attacks
        if secrets.compare_digest(stored_hash, submitted_hash):
            # Success: invalidate OTP immediately to prevent reuse
            cache.delete(keys["otp_hash"])
            cache.delete(keys["attempts"])
            cache.delete(keys["cooldown"])
            return True, "احراز هویت با موفقیت انجام شد."

        # Increment failed attempts
        cache.set(keys["attempts"], attempts + 1, timeout=cls.OTP_EXPIRY_SECONDS)
        remaining = cls.MAX_ATTEMPTS - (attempts + 1)
        return False, f"کد وارد شده اشتباه است. ({remaining} تلاش باقی‌مانده)"

    @classmethod
    def _dispatch_sms(cls, phone: str, code: str):
        """
        SMS Provider handler. Outputs to console in development.
        """
        # In Development:
        print(
            f"\n{'='*50}\n[SMS SERVICE MOCK]\nTo: {phone}\nOTP Code: {code}\n{'='*50}\n"
        )
        # In Production: Hook to SMS Gateway API (Kavenegar, Ghasedak, etc.)
