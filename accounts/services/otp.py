# accounts/services/otp.py
import hashlib
import hmac
import logging
import secrets
import time
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


class OTPService:
    OTP_EXPIRY_SECONDS = 180
    COOLDOWN_SECONDS = 120
    MAX_ATTEMPTS = 5
    MAX_DAILY_REQUESTS = 10

    @classmethod
    def _get_cache_keys(cls, phone: str) -> dict:
        today_str = timezone.localdate().strftime("%Y-%m-%d")
        return {
            "otp_hash": f"otp:hash:{phone}",
            "cooldown_end": f"otp:cooldown:{phone}",
            "attempts": f"otp:attempts:{phone}",
            "daily_count": f"otp:daily:{phone}:{today_str}",
        }

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """استانداردسازی ارقام فارسی/عربی و حذف کاراکترهای اضافه"""
        if not phone:
            return ""
        persian_digits = "۰۱۲۳۴۵۶۷۸۹"
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        english_digits = "0123456789"
        trans = str.maketrans(persian_digits + arabic_digits, english_digits * 2)
        phone = phone.translate(trans).strip().replace(" ", "").replace("-", "")

        if phone.startswith("+98"):
            phone = "0" + phone[3:]
        elif phone.startswith("98"):
            phone = "0" + phone[2:]
        elif len(phone) == 10 and phone.startswith("9"):
            phone = "0" + phone

        return phone

    @classmethod
    def _hash_code(cls, phone: str, code: str) -> str:
        """تولید هش امن HMAC-SHA256 وابسته به Secret Key پروژه"""
        key = settings.SECRET_KEY.encode("utf-8")
        msg = f"{phone}:{code}".encode("utf-8")
        return hmac.new(key, msg, hashlib.sha256).hexdigest()

    @classmethod
    def _dispatch_sms(cls, phone: str, code: str) -> bool:
        """ارسال پیامک از طریق درگاه / لاگ سیستم در محیط توسعه"""
        logger.info(f"[OTP Service] OTP code for {phone}: {code}")
        # اینجا لاجیک اتصال به Kavenegar / FarazSMS قرار می‌گیرد
        return True

    @classmethod
    def send_otp(cls, raw_phone: str) -> tuple[bool, str, int]:
        """ارسال یا بازفرستادن کد یکبار مصرف با کنترل Cooldown و سهمیه روزانه"""
        phone = cls.normalize_phone(raw_phone)
        if not (phone.startswith("09") and len(phone) == 11 and phone.isdigit()):
            return False, "شماره موبایل وارد شده معتبر نیست (مثال: ۰۹۱۲۳۴۵۶۷۸۹)", 0

        keys = cls._get_cache_keys(phone)

        # ۱. بررسی زمان انتظار مجدد (Cooldown)
        cooldown_end = cache.get(keys["cooldown_end"])
        if cooldown_end:
            remaining = int(cooldown_end - time.time())
            if remaining > 0:
                return (
                    False,
                    f"لطفاً {remaining} ثانیه دیگر مجدداً تلاش کنید.",
                    remaining,
                )

        # ۲. بررسی سقف ارسال روزانه
        daily_count = cache.get(keys["daily_count"], 0)
        if daily_count >= cls.MAX_DAILY_REQUESTS:
            return (
                False,
                "تعداد درخواست‌های پیامک برای این شماره در امروز به سقف مجاز رسیده است.",
                0,
            )

        # ۳. تولید کد تصادفی ۶ رقمی ایمن
        code = str(secrets.randbelow(900000) + 100000)
        print("\n" + "=" * 50)
        print(f"🔑 [DEBUG OTP] Phone: {phone} | Code: {code}")
        print("=" * 50 + "\n")
        code_hash = cls._hash_code(phone, code)

        # ۴. ذخیره در کش
        now = time.time()
        cache.set(keys["otp_hash"], code_hash, timeout=cls.OTP_EXPIRY_SECONDS)
        cache.set(
            keys["cooldown_end"],
            now + cls.COOLDOWN_SECONDS,
            timeout=cls.COOLDOWN_SECONDS,
        )
        cache.set(keys["attempts"], 0, timeout=cls.OTP_EXPIRY_SECONDS)

        # سهمیه روزانه (با طول عمر ۲۴ ساعت)
        try:
            cache.incr(keys["daily_count"])
        except ValueError:
            cache.set(keys["daily_count"], 1, timeout=86400)

        # ۵. ارسال پیامک با مدیریت خطا و رول‌بک کش
        try:
            sms_sent = cls._dispatch_sms(phone, code)
            if not sms_sent:
                raise Exception("SMS gateway failed.")
        except Exception as err:
            logger.exception(f"Failed to dispatch SMS to {phone}: {err}")
            cache.delete_many(
                [keys["otp_hash"], keys["cooldown_end"], keys["attempts"]]
            )
            return False, "خطا در ارسال پیامک. لطفاً دقایقی دیگر تلاش کنید.", 0

        return (
            True,
            f"کد تأیید به شماره {phone} پیامک شد.",
            cls.COOLDOWN_SECONDS,
        )

    @classmethod
    def verify_otp(cls, raw_phone: str, code: str) -> tuple[bool, str]:
        """اعتبارسنجی کد ارسالی و بررسی محافظت Brute-force"""
        phone = cls.normalize_phone(raw_phone)
        code = cls.normalize_phone(code)

        if not phone or not code or len(code) != 6:
            return False, "کد وارد شده باید ۶ رقم باشد."

        keys = cls._get_cache_keys(phone)
        stored_hash = cache.get(keys["otp_hash"])

        if not stored_hash:
            return (
                False,
                "کد تأیید منقضی شده یا درخواست نشده است. لطفاً مجدداً درخواست کد دهید.",
            )

        attempts = cache.get(keys["attempts"], 0)

        # بررسی کد با تابع امن زمانی secrets.compare_digest
        user_hash = cls._hash_code(phone, code)
        if secrets.compare_digest(stored_hash, user_hash):
            # کد صحیح است -> پاکسازی وضعیت کد و تلاش‌ها
            cache.delete_many([keys["otp_hash"], keys["attempts"]])
            return True, "کد تأیید شد."

        # کد اشتباه است -> افزایش تعداد تلاش‌های ناموفق
        attempts += 1
        if attempts >= cls.MAX_ATTEMPTS:
            cache.delete_many([keys["otp_hash"], keys["attempts"]])
            return (
                False,
                "تعداد تلاش‌های ناموفق بیش از حد مجاز بود. کد باطل شد، لطفاً مجدداً کد دریافت کنید.",
            )

        cache.set(keys["attempts"], attempts, timeout=cls.OTP_EXPIRY_SECONDS)
        remaining = cls.MAX_ATTEMPTS - attempts
        return False, f"کد وارد شده اشتباه است. ({remaining} تلاش باقی‌مانده)"
