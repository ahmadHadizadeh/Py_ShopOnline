# accounts/services/otp.py
import hashlib
import json
import logging
import random
import re
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class SMSIRService:

    ENDPOINT = "https://api.sms.ir/v1/send/verify"
    TIMEOUT = 10
    DEFAULT_TEMPLATE_ID = 358253

    CODE_LENGTH = 6
    EXPIRATION_SECONDS = 120
    COOLDOWN_SECONDS = 120
    MAX_VERIFY_ATTEMPTS = 5

    CACHE_PREFIX_CODE = "otp_code:"
    CACHE_PREFIX_COOLDOWN = "otp_cooldown:"
    CACHE_PREFIX_ATTEMPTS = "otp_attempts:"

    # -------------------------------------------------------------------------
    # تنظیمات و نرمال‌سازی شماره
    # -------------------------------------------------------------------------
    @classmethod
    def _get_config(cls) -> Tuple[str, int]:
        cfg = getattr(settings, "SMSIR_CONFIG", {}) or {}
        api_key = (
            cfg.get("API_KEY") or getattr(settings, "SMSIR_API_KEY", "") or ""
        ).strip()
        template_id = (
            cfg.get("TEMPLATE_ID")
            or getattr(settings, "SMSIR_TEMPLATE_ID", cls.DEFAULT_TEMPLATE_ID)
            or cls.DEFAULT_TEMPLATE_ID
        )
        try:
            template_id = int(template_id)
        except (TypeError, ValueError):
            template_id = cls.DEFAULT_TEMPLATE_ID

        return api_key, template_id

    @staticmethod
    def normalize_phone(phone: str) -> Optional[str]:
        if not phone or not isinstance(phone, str):
            return None

        # تبدیل ارقام فارسی و عربی به انگلیسی
        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        ar_digits = "٠١٢٣٤٥٦٧٨٩"
        en_digits = "0123456789"
        trans_table = str.maketrans(fa_digits + ar_digits, en_digits * 2)
        phone = phone.translate(trans_table)

        # حذف کاراکترهای غیر رقمی به جز بعلاوه ابتدایی
        phone = phone.strip()
        has_plus = phone.startswith("+")
        digits_only = re.sub(r"\D", "", phone)

        if not digits_only:
            return None

        if has_plus:
            digits_only = "+" + digits_only

        if digits_only.startswith("+98"):
            digits_only = "0" + digits_only[3:]
        elif digits_only.startswith("98") and len(digits_only) == 12:
            digits_only = "0" + digits_only[2:]
        elif digits_only.startswith("9") and len(digits_only) == 10:
            digits_only = "0" + digits_only

        if len(digits_only) == 11 and digits_only.startswith("09"):
            return digits_only

        return None

    # سازگاری با کد قدیمی
    _normalize_mobile = normalize_phone

    # -------------------------------------------------------------------------
    # کش و کلیدها
    # -------------------------------------------------------------------------
    @classmethod
    def _hash_code(cls, phone: str, code: str) -> str:
        secret = getattr(settings, "SECRET_KEY", "otp-secret")
        payload = f"{phone}:{code}:{secret}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def get_remaining_cooldown(cls, phone: str) -> int:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return 0
        cd_key = f"{cls.CACHE_PREFIX_COOLDOWN}{norm_phone}"
        remaining = cache.get(cd_key)
        return int(remaining) if remaining is not None else 0

    # -------------------------------------------------------------------------
    # ارسال خام SMS.ir
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # ارسال خام SMS.ir
    # -------------------------------------------------------------------------
    @classmethod
    def send_verification_code(cls, mobile: str, code: str) -> Tuple[bool, str]:
        norm_mobile = cls.normalize_phone(mobile)
        if not norm_mobile:
            return False, "شماره موبایل وارد شده نامعتبر است."

        # =========================================================================
        # ⚠️ تنظیم موقت برای محیط توسعه (Development Mode)
        # برای حالت پروداکشن و ارسال واقعی پیامک، مقدار زیر را False بگذارید.
        # =========================================================================
        DEBUG_OTP_CONSOLE = True

        if DEBUG_OTP_CONSOLE:
            print("\n" + "=" * 50)
            print(f"🔑 [OTP CONSOLE] Phone: {norm_mobile}  |  Code: {code}")
            print("=" * 50 + "\n")
            return True, "کد تأیید به صورت تستی تولید شد."
        # =========================================================================

        api_key, template_id = cls._get_config()

        # محیط تست یا نبود کلید
        if not api_key:
            logger.warning(
                f"[SMS.ir MOCK] SMSIR_API_KEY تنظیم نشده است. "
                f"کد تستی برای {norm_mobile}: {code}"
            )
            return True, f"[تست/کنسول] کد شما: {code}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "x-api-key": api_key,
        }
        payload = {
            "mobile": norm_mobile,
            "templateId": template_id,
            "parameters": [
                {"name": "CODE", "value": str(code)},
            ],
        }

        try:
            response = requests.post(
                cls.ENDPOINT,
                json=payload,
                headers=headers,
                timeout=cls.TIMEOUT,
            )
        except requests.exceptions.Timeout:
            logger.error(f"[SMS.ir] Timeout error sending to {norm_mobile}")
            return False, "خطا در برقراری ارتباط با سامانه پیامک (Timeout)."
        except requests.exceptions.RequestException as e:
            logger.error(f"[SMS.ir] Request exception for {norm_mobile}: {e}")
            return False, "خطا در ارسال پیامک. لطفاً بعداً تلاش کنید."

        if response.status_code != 200:
            logger.error(
                f"[SMS.ir] HTTP {response.status_code} for {norm_mobile}: {response.text}"
            )
            return False, f"سامانه پیامک با خطای {response.status_code} پاسخ داد."

        try:
            data = response.json()
        except ValueError:
            logger.error(f"[SMS.ir] Invalid JSON: {response.text}")
            return False, "پاسخ نامعتبر از سامانه پیامک دریافت شد."

        if data.get("status") == 1:
            if data.get("status") == 1:
                logger.info("[SMS.ir] Code sent to %s. data=%s", norm_mobile, data)
                return True, "کد تایید پیامک شد."

            return True, "کد تأیید با موفقیت پیامک شد."

        msg = data.get("message") or "ارسال پیامک توسط سرویس‌دهنده انجام نشد."
        logger.warning(f"[SMS.ir] Failed for {norm_mobile}: {data}")
        return False, msg

    # -------------------------------------------------------------------------
    # منطق اصلی OTP (تولید، ثبت در کش و ارسال)
    # -------------------------------------------------------------------------
    @classmethod
    def send_otp(cls, phone: str) -> Tuple[bool, str, int]:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return False, "شماره موبایل وارد شده نامعتبر است (فرمت: 09xxxxxxxxx).", 0

        # کنترل محدودیت زمانی ارسال مجدد (Cooldown)
        cooldown_left = cls.get_remaining_cooldown(norm_phone)
        if cooldown_left > 0:
            return (
                False,
                f"لطفاً {cooldown_left} ثانیه دیگر مجدداً تلاش کنید.",
                cooldown_left,
            )

        # تولید کد ۶ رقمی یکپارچه با قالب
        code = f"{random.randint(100000, 999999)}"

        # ارسال از طریق پیامک
        sent_ok, send_msg = cls.send_verification_code(norm_phone, code)
        if not sent_ok:
            return False, send_msg, 0

        # ذخیره در کش
        code_key = f"{cls.CACHE_PREFIX_CODE}{norm_phone}"
        hashed_code = cls._hash_code(norm_phone, code)
        cache.set(code_key, hashed_code, timeout=cls.EXPIRATION_SECONDS)

        # ثبت زمان کول‌داون
        cd_key = f"{cls.CACHE_PREFIX_COOLDOWN}{norm_phone}"
        cache.set(cd_key, cls.COOLDOWN_SECONDS, timeout=cls.COOLDOWN_SECONDS)

        # ریست شمارنده تلاش‌های ناموفق
        attempts_key = f"{cls.CACHE_PREFIX_ATTEMPTS}{norm_phone}"
        cache.delete(attempts_key)

        return True, "کد تأیید ۶ رقمی با موفقیت ارسال شد.", cls.COOLDOWN_SECONDS

    # -------------------------------------------------------------------------
    # بررسی و تأیید OTP
    # -------------------------------------------------------------------------
    @classmethod
    def verify_otp(cls, phone: str, entered_code: str) -> Tuple[bool, str]:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return False, "شماره موبایل نامعتبر است."

        if not entered_code:
            return False, "کد تأیید را وارد کنید."

        # تبدیل ارقام احتمالی فارسی کد به انگلیسی
        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        ar_digits = "٠١٢٣٤٥٦٧٨٩"
        en_digits = "0123456789"
        trans_table = str.maketrans(fa_digits + ar_digits, en_digits * 2)
        entered_code = str(entered_code).translate(trans_table).strip()

        if len(entered_code) != cls.CODE_LENGTH or not entered_code.isdigit():
            return False, f"کد تأیید باید دقیقاً {cls.CODE_LENGTH} رقم باشد."

        attempts_key = f"{cls.CACHE_PREFIX_ATTEMPTS}{norm_phone}"
        attempts = cache.get(attempts_key, 0)
        if attempts >= cls.MAX_VERIFY_ATTEMPTS:
            return (
                False,
                "تعداد تلاش‌های ناموفق بیش از حد مجاز است. لطفاً کد جدید دریافت کنید.",
            )

        code_key = f"{cls.CACHE_PREFIX_CODE}{norm_phone}"
        cached_hash = cache.get(code_key)

        if not cached_hash:
            return False, "کد تأیید منقضی شده یا هنوز درخواستی ثبت نشده است."

        input_hash = cls._hash_code(norm_phone, entered_code)
        if cached_hash != input_hash:
            cache.set(attempts_key, attempts + 1, timeout=cls.EXPIRATION_SECONDS)
            remaining_tries = max(0, cls.MAX_VERIFY_ATTEMPTS - (attempts + 1))
            return (
                False,
                f"کد تأیید وارد شده اشتباه است. ({remaining_tries} فرصت باقی‌مانده)",
            )

        # کد صحیح: پاکسازی کش
        cache.delete(code_key)
        cache.delete(attempts_key)
        return True, "کد تأیید با موفقیت احراز شد."
