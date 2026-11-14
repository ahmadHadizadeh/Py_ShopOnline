# accounts/services/otp.py
import hashlib
import hmac
import json
import logging
import math
import secrets
import re
from datetime import timedelta
from typing import Optional, Tuple
from uuid import uuid4

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import OTPChallenge

logger = logging.getLogger(__name__)


class SMSIRService:

    ENDPOINT = "https://api.sms.ir/v1/send/verify"
    TIMEOUT = 10
    DEFAULT_TEMPLATE_ID = 358253

    CODE_LENGTH = 6
    EXPIRATION_SECONDS = 120
    COOLDOWN_SECONDS = 120
    MAX_VERIFY_ATTEMPTS = 5

    SEND_LOCK_SECONDS = 30

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
    # شناسه‌های امن و وضعیت اتمیک OTP
    # -------------------------------------------------------------------------
    @classmethod
    def _hash_phone(cls, phone: str) -> str:
        secret = str(settings.SECRET_KEY).encode("utf-8")
        payload = f"otp-phone:{phone}".encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    @classmethod
    def _hash_code(cls, phone: str, code: str) -> str:
        secret = str(settings.SECRET_KEY).encode("utf-8")
        payload = f"otp-code:{phone}:{code}".encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _seconds_until(deadline, now=None) -> int:
        if deadline is None:
            return 0
        now = now or timezone.now()
        return max(0, math.ceil((deadline - now).total_seconds()))

    @classmethod
    def _get_locked_challenge(cls, phone_hash: str) -> OTPChallenge:
        challenge, _ = OTPChallenge.objects.get_or_create(phone_hash=phone_hash)
        return OTPChallenge.objects.select_for_update().get(pk=challenge.pk)

    @classmethod
    def get_remaining_cooldown(cls, phone: str) -> int:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return 0

        challenge = OTPChallenge.objects.filter(
            phone_hash=cls._hash_phone(norm_phone)
        ).only("send_lock_token", "send_lock_expires_at", "cooldown_until").first()
        if challenge is None:
            return 0

        now = timezone.now()
        if (
            challenge.send_lock_token is not None
            and challenge.send_lock_expires_at is not None
            and challenge.send_lock_expires_at > now
        ):
            return cls._seconds_until(challenge.send_lock_expires_at, now)

        return cls._seconds_until(challenge.cooldown_until, now)

    # -------------------------------------------------------------------------
    # ارسال خام SMS.ir
    # -------------------------------------------------------------------------
    @classmethod
    def send_verification_code(cls, mobile: str, code: str) -> Tuple[bool, str]:
        norm_mobile = cls.normalize_phone(mobile)
        if not norm_mobile:
            return False, "شماره موبایل وارد شده نامعتبر است."

        # =========================================================================
        # Development: console OTP
        # Production: real SMS.ir Verify API
        # =========================================================================
        DEBUG_OTP_CONSOLE = bool(getattr(settings, "DEBUG", False))

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
                logger.info( "[SMS.ir] Code sent to %s. data=%s", norm_mobile, data)
                return True, "کد تایید پیامک شد."

            return True, "کد تأیید با موفقیت پیامک شد."

        msg = data.get("message") or "ارسال پیامک توسط سرویس‌دهنده انجام نشد."
        logger.warning(f"[SMS.ir] Failed for {norm_mobile}: {data}")
        return False, msg

    # -------------------------------------------------------------------------
    # منطق اصلی OTP (رزرو اتمیک، ارسال و نهایی‌سازی)
    # -------------------------------------------------------------------------
    @classmethod
    def send_otp(cls, phone: str) -> Tuple[bool, str, int]:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return False, "شماره موبایل وارد شده نامعتبر است (فرمت: 09xxxxxxxxx).", 0

        phone_hash = cls._hash_phone(norm_phone)
        code = str(secrets.randbelow(900000) + 100000)
        token = uuid4()
        now = timezone.now()

        # Reserve the phone's send slot under a database row lock. The SMS
        # request itself stays outside the transaction and cannot block DB rows.
        with transaction.atomic():
            challenge = cls._get_locked_challenge(phone_hash)
            now = timezone.now()

            if (
                challenge.send_lock_token is not None
                and challenge.send_lock_expires_at is not None
                and challenge.send_lock_expires_at > now
            ):
                remaining = cls._seconds_until(
                    challenge.send_lock_expires_at, now
                )
                return (
                    False,
                    "درخواست ارسال کد قبلی هنوز در حال انجام است؛ کمی بعد دوباره تلاش کنید.",
                    remaining,
                )

            cooldown_left = cls._seconds_until(challenge.cooldown_until, now)
            if cooldown_left > 0:
                return (
                    False,
                    f"لطفاً {cooldown_left} ثانیه دیگر مجدداً تلاش کنید.",
                    cooldown_left,
                )

            challenge.send_lock_token = token
            challenge.send_lock_expires_at = now + timedelta(
                seconds=cls.SEND_LOCK_SECONDS
            )
            # Persist the hash before contacting SMS.ir. If the worker exits
            # after the provider accepted the message but before finalization,
            # the delivered code can still be verified after the short send lock.
            challenge.code_hash = cls._hash_code(norm_phone, code)
            challenge.expires_at = now + timedelta(
                seconds=cls.SEND_LOCK_SECONDS + cls.EXPIRATION_SECONDS
            )
            challenge.failed_attempts = 0
            challenge.save(
                update_fields=[
                    "send_lock_token",
                    "send_lock_expires_at",
                    "code_hash",
                    "expires_at",
                    "failed_attempts",
                    "updated_at",
                ]
            )
            challenge_id = challenge.pk

        # A timeout or interrupted provider response is ambiguous: the
        # provider may already have accepted the SMS. Keep the reservation until
        # its short expiry on every unsuccessful/exceptional outcome so a second
        # request cannot immediately produce a duplicate message. The code hash
        # remains usable after the reservation expires.
        sent_ok, send_msg = cls.send_verification_code(norm_phone, code)
        if not sent_ok:
            failed_at = timezone.now()
            remaining = cls._seconds_until(
                failed_at + timedelta(seconds=cls.SEND_LOCK_SECONDS),
                failed_at,
            )
            return (
                False,
                f"{send_msg} برای جلوگیری از ارسال تکراری، لطفاً {remaining} ثانیه دیگر تلاش کنید.",
                remaining,
            )

        sent_at = timezone.now()
        with transaction.atomic():
            challenge = OTPChallenge.objects.select_for_update().get(
                pk=challenge_id
            )
            # Never let an old, delayed sender overwrite a newer reservation.
            if challenge.send_lock_token != token:
                return (
                    False,
                    "وضعیت ارسال کد تغییر کرده است؛ لطفاً دوباره درخواست دهید.",
                    0,
                )

            challenge.cooldown_until = sent_at + timedelta(
                seconds=cls.COOLDOWN_SECONDS
            )
            challenge.expires_at = sent_at + timedelta(
                seconds=cls.EXPIRATION_SECONDS
            )
            challenge.send_lock_token = None
            challenge.send_lock_expires_at = None
            challenge.save(
                update_fields=[
                    "cooldown_until",
                    "expires_at",
                    "send_lock_token",
                    "send_lock_expires_at",
                    "updated_at",
                ]
            )

        return True, "کد تأیید ۶ رقمی با موفقیت ارسال شد.", cls.COOLDOWN_SECONDS

    # -------------------------------------------------------------------------
    # بررسی و تأیید OTP
    # -------------------------------------------------------------------------
    @classmethod
    @transaction.atomic
    def verify_otp(cls, phone: str, entered_code: str) -> Tuple[bool, str]:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return False, "شماره موبایل نامعتبر است."

        if not entered_code:
            return False, "کد تأیید را وارد کنید."

        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        ar_digits = "٠١٢٣٤٥٦٧٨٩"
        en_digits = "0123456789"
        trans_table = str.maketrans(fa_digits + ar_digits, en_digits * 2)
        entered_code = str(entered_code).translate(trans_table).strip()

        if len(entered_code) != cls.CODE_LENGTH or not entered_code.isdigit():
            return False, f"کد تأیید باید دقیقاً {cls.CODE_LENGTH} رقم باشد."

        phone_hash = cls._hash_phone(norm_phone)
        challenge = (
            OTPChallenge.objects.select_for_update()
            .filter(phone_hash=phone_hash)
            .first()
        )
        if challenge is None:
            return False, "کد تأیید منقضی شده یا هنوز درخواستی ثبت نشده است."

        now = timezone.now()
        if (
            challenge.send_lock_token is not None
            and challenge.send_lock_expires_at is not None
            and challenge.send_lock_expires_at > now
        ):
            remaining = cls._seconds_until(
                challenge.send_lock_expires_at, now
            )
            return (
                False,
                f"ارسال کد هنوز در حال انجام است؛ لطفاً {remaining} ثانیه دیگر تلاش کنید.",
            )

        stale_send_lock = challenge.send_lock_token is not None
        if challenge.failed_attempts >= cls.MAX_VERIFY_ATTEMPTS:
            if stale_send_lock:
                challenge.send_lock_token = None
                challenge.send_lock_expires_at = None
                challenge.save(
                    update_fields=[
                        "send_lock_token",
                        "send_lock_expires_at",
                        "updated_at",
                    ]
                )
            return (
                False,
                "تعداد تلاش‌های ناموفق بیش از حد مجاز است. لطفاً کد جدید دریافت کنید.",
            )

        if (
            not challenge.code_hash
            or challenge.expires_at is None
            or challenge.expires_at <= now
        ):
            challenge.code_hash = ""
            challenge.expires_at = None
            challenge.failed_attempts = 0
            challenge.send_lock_token = None
            challenge.send_lock_expires_at = None
            challenge.save(
                update_fields=[
                    "code_hash",
                    "expires_at",
                    "failed_attempts",
                    "send_lock_token",
                    "send_lock_expires_at",
                    "updated_at",
                ]
            )
            return False, "کد تأیید منقضی شده یا هنوز درخواستی ثبت نشده است."

        input_hash = cls._hash_code(norm_phone, entered_code)
        if not hmac.compare_digest(challenge.code_hash, input_hash):
            challenge.failed_attempts += 1
            challenge.send_lock_token = None
            challenge.send_lock_expires_at = None
            challenge.save(
                update_fields=[
                    "failed_attempts",
                    "send_lock_token",
                    "send_lock_expires_at",
                    "updated_at",
                ]
            )
            remaining_tries = max(
                0, cls.MAX_VERIFY_ATTEMPTS - challenge.failed_attempts
            )
            return (
                False,
                f"کد تأیید وارد شده اشتباه است. ({remaining_tries} فرصت باقی‌مانده)",
            )

        # The row lock makes consumption single-use across application workers.
        challenge.code_hash = ""
        challenge.expires_at = None
        challenge.failed_attempts = 0
        challenge.send_lock_token = None
        challenge.send_lock_expires_at = None
        challenge.save(
            update_fields=[
                "code_hash",
                "expires_at",
                "failed_attempts",
                "send_lock_token",
                "send_lock_expires_at",
                "updated_at",
            ]
        )
        return True, "کد تأیید با موفقیت احراز شد."
