# accounts/services/otp.py
import hashlib
import hmac
import logging
import math
import re
import secrets
import threading
import time
from contextlib import contextmanager
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction

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

    LOCK_BUSY_MESSAGE = "درخواست دیگری برای این شماره در حال پردازش است؛ کمی بعد تلاش کنید."

    # SQLite is used only for local development/tests. Production uses
    # PostgreSQL advisory locks, shared by all Gunicorn workers.
    _local_otp_lock = threading.Lock()

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
    def _advisory_lock_id(cls, phone: str) -> int:
        digest = hashlib.blake2b(
            f"pyshop:otp:{phone}".encode("utf-8"),
            digest_size=8,
        ).digest()
        return int.from_bytes(digest, byteorder="big", signed=True)

    @classmethod
    @contextmanager
    def _otp_lock(cls, phone: str, *, transaction_scoped: bool = False):
        """
        Serialize OTP operations for one phone across application workers.

        PostgreSQL advisory locks are shared by all Gunicorn workers. Sending
        holds a session lock across the SMS request; verification uses a
        transaction lock so cache changes commit before the lock is released.
        SQLite falls back to a process-local lock for development and tests.
        """
        if connection.vendor == "postgresql":
            lock_id = cls._advisory_lock_id(phone)

            if transaction_scoped:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_try_advisory_xact_lock(%s)",
                            [lock_id],
                        )
                        acquired = bool(cursor.fetchone()[0])
                    yield acquired
                return

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    [lock_id],
                )
                acquired = bool(cursor.fetchone()[0])

            if not acquired:
                yield False
                return

            try:
                yield True
            finally:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s)",
                            [lock_id],
                        )
                        released = bool(cursor.fetchone()[0])
                    if not released:
                        logger.error(
                            "[OTP] PostgreSQL advisory lock was not held "
                            "when release was attempted."
                        )
                except Exception:
                    # PostgreSQL releases session locks when a lost connection
                    # is closed, so this is safe to log and leave to the server.
                    logger.exception("[OTP] Failed to release advisory lock.")
            return

        if connection.vendor != "sqlite":
            raise RuntimeError(
                "Atomic OTP locking supports PostgreSQL and SQLite only."
            )

        acquired = cls._local_otp_lock.acquire(blocking=False)
        if not acquired:
            yield False
            return

        try:
            if transaction_scoped:
                # Release only after the SQLite transaction has completed.
                with transaction.atomic():
                    yield True
            else:
                yield True
        finally:
            cls._local_otp_lock.release()

    @classmethod
    def get_remaining_cooldown(cls, phone: str) -> int:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return 0

        cd_key = f"{cls.CACHE_PREFIX_COOLDOWN}{norm_phone}"
        remaining = cache.get(cd_key)
        if remaining is None:
            return 0

        try:
            value = float(remaining)
        except (TypeError, ValueError):
            return 0

        # New entries store an absolute expiry timestamp for a real countdown.
        # Small values are legacy cache entries and remain valid until their
        # original cache TTL expires.
        if value >= 1_000_000_000:
            return max(0, math.ceil(value - time.time()))

        return max(0, int(value))

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
    # منطق اصلی OTP (تولید، ثبت در کش و ارسال)
    # -------------------------------------------------------------------------
    @classmethod
    def send_otp(cls, phone: str) -> Tuple[bool, str, int]:
        norm_phone = cls.normalize_phone(phone)
        if not norm_phone:
            return False, "شماره موبایل وارد شده نامعتبر است (فرمت: 09xxxxxxxxx).", 0

        with cls._otp_lock(norm_phone) as lock_acquired:
            if not lock_acquired:
                return False, cls.LOCK_BUSY_MESSAGE, 0

            cooldown_left = cls.get_remaining_cooldown(norm_phone)
            if cooldown_left > 0:
                return (
                    False,
                    f"لطفاً {cooldown_left} ثانیه دیگر مجدداً تلاش کنید.",
                    cooldown_left,
                )

            code = str(secrets.randbelow(900000) + 100000)

            # Keep the per-phone lock while talking to SMS.ir. A second worker
            # cannot pass the cooldown check and send another code concurrently.
            sent_ok, send_msg = cls.send_verification_code(norm_phone, code)
            if not sent_ok:
                return False, send_msg, 0

            code_key = f"{cls.CACHE_PREFIX_CODE}{norm_phone}"
            cd_key = f"{cls.CACHE_PREFIX_COOLDOWN}{norm_phone}"
            attempts_key = f"{cls.CACHE_PREFIX_ATTEMPTS}{norm_phone}"
            hashed_code = cls._hash_code(norm_phone, code)

            # Commit the hash, cooldown, and attempt reset as one unit.
            with transaction.atomic():
                cache.set(
                    code_key,
                    hashed_code,
                    timeout=cls.EXPIRATION_SECONDS,
                )
                cache.set(
                    cd_key,
                    time.time() + cls.COOLDOWN_SECONDS,
                    timeout=cls.COOLDOWN_SECONDS,
                )
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

        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        ar_digits = "٠١٢٣٤٥٦٧٨٩"
        en_digits = "0123456789"
        trans_table = str.maketrans(fa_digits + ar_digits, en_digits * 2)
        entered_code = str(entered_code).translate(trans_table).strip()

        if len(entered_code) != cls.CODE_LENGTH or not entered_code.isdigit():
            return False, f"کد تأیید باید دقیقاً {cls.CODE_LENGTH} رقم باشد."

        with cls._otp_lock(norm_phone, transaction_scoped=True) as lock_acquired:
            if not lock_acquired:
                return False, cls.LOCK_BUSY_MESSAGE

            attempts_key = f"{cls.CACHE_PREFIX_ATTEMPTS}{norm_phone}"
            try:
                attempts = int(cache.get(attempts_key, 0) or 0)
            except (TypeError, ValueError):
                attempts = 0

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
            if not hmac.compare_digest(str(cached_hash), input_hash):
                next_attempts = attempts + 1
                cache.set(
                    attempts_key,
                    next_attempts,
                    timeout=cls.EXPIRATION_SECONDS,
                )
                remaining_tries = max(
                    0,
                    cls.MAX_VERIFY_ATTEMPTS - next_attempts,
                )
                return (
                    False,
                    f"کد تأیید وارد شده اشتباه است. ({remaining_tries} فرصت باقی‌مانده)",
                )

            # The transaction-level advisory lock remains held until this
            # deletion commits, so another worker cannot accept the same code.
            cache.delete(code_key)
            cache.delete(attempts_key)
            return True, "کد تأیید با موفقیت احراز شد."
