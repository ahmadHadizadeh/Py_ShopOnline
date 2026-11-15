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
from django.db import DatabaseError, connection, transaction

logger = logging.getLogger(__name__)


class SMSDeliveryUncertain(Exception):
    """The provider may have accepted an SMS but its outcome was not confirmed."""


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
    CACHE_KEY_HMAC_DOMAIN = "pyshop:otp-cache:v1"

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
    def _cache_phone_identifier(cls, phone: str) -> str:
        """Return a non-reversible, secret-keyed identifier for cache keys."""
        secret = str(settings.SECRET_KEY).encode("utf-8")
        message = f"{cls.CACHE_KEY_HMAC_DOMAIN}:{phone}".encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    @classmethod
    def _cache_key(cls, prefix: str, phone: str) -> str:
        return f"{prefix}{cls._cache_phone_identifier(phone)}"

    @classmethod
    def _legacy_cache_key(cls, prefix: str, phone: str) -> str:
        """Address pre-HMAC entries only during the short compatibility window."""
        return f"{prefix}{phone}"

    @staticmethod
    def _cooldown_remaining_from_value(value) -> int:
        if value is None:
            return 0

        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0

        # Current entries store an absolute expiry timestamp. Older entries
        # stored a duration and relied on the cache TTL for their actual expiry.
        if value >= 1_000_000_000:
            return max(0, math.ceil(value - time.time()))

        return max(0, int(value))

    @classmethod
    def _migrate_legacy_cooldown(cls, phone: str) -> None:
        """
        Move a legacy cooldown to its HMAC key before deleting the raw key.

        This runs inside verify_otp's transaction. Read-back is required because
        Django DatabaseCache may silently swallow a failed write.
        """
        legacy_key = cls._legacy_cache_key(cls.CACHE_PREFIX_COOLDOWN, phone)
        legacy_value = cache.get(legacy_key)
        if legacy_value is None:
            return

        hashed_key = cls._cache_key(cls.CACHE_PREFIX_COOLDOWN, phone)
        hashed_value = cache.get(hashed_key)
        remaining = max(
            cls._cooldown_remaining_from_value(legacy_value),
            cls._cooldown_remaining_from_value(hashed_value),
        )

        if remaining > 0:
            expires_at = time.time() + remaining
            cache.set(hashed_key, expires_at, timeout=remaining)
            if cache.get(hashed_key) != expires_at:
                raise DatabaseError(
                    "OTP cooldown migration could not be verified."
                )
        else:
            cache.delete(hashed_key)

        cache.delete(legacy_key)

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
                            "SELECT pg_advisory_xact_lock(%s)",
                            [lock_id],
                        )
                    # Verification is a short critical section. Queue concurrent
                    # attempts so each one is counted rather than discarded.
                    yield True
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

        acquired = cls._local_otp_lock.acquire(
            blocking=transaction_scoped,
        )
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

        # Prefer the HMAC identifier, but honor an in-flight challenge created
        # by the pre-HMAC release until it is verified or expires.
        hashed_key = cls._cache_key(cls.CACHE_PREFIX_COOLDOWN, norm_phone)
        legacy_key = cls._legacy_cache_key(cls.CACHE_PREFIX_COOLDOWN, norm_phone)
        return max(
            cls._cooldown_remaining_from_value(cache.get(hashed_key)),
            cls._cooldown_remaining_from_value(cache.get(legacy_key)),
        )

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
        except requests.exceptions.Timeout as exc:
            logger.warning("[SMS.ir] Delivery outcome is unknown after a timeout.")
            raise SMSDeliveryUncertain from exc
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "[SMS.ir] Delivery outcome is unknown after %s.",
                type(exc).__name__,
            )
            raise SMSDeliveryUncertain from exc

        if response.status_code != 200:
            logger.error(
                "[SMS.ir] HTTP %s while sending verification message.",
                response.status_code,
            )
            if 400 <= response.status_code < 500 and response.status_code not in {408, 425, 429}:
                return False, f"سامانه پیامک درخواست را نپذیرفت (HTTP {response.status_code})."
            raise SMSDeliveryUncertain(
                f"SMS.ir returned HTTP {response.status_code}; delivery is not confirmed."
            )

        try:
            data = response.json()
        except ValueError as exc:
            logger.error("[SMS.ir] Provider returned a non-JSON response.")
            raise SMSDeliveryUncertain from exc

        if not isinstance(data, dict) or "status" not in data:
            logger.error("[SMS.ir] Provider returned an invalid response structure.")
            raise SMSDeliveryUncertain("SMS.ir returned an invalid response structure.")

        if data.get("status") == 1:
            logger.info("[SMS.ir] Verification message accepted by provider.")
            return True, "کد تأیید پیامک شد."

        msg = data.get("message") or "ارسال پیامک توسط سرویس‌دهنده انجام نشد."
        logger.warning("[SMS.ir] Provider rejected the verification message.")
        return False, str(msg)

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
            code_key = cls._cache_key(cls.CACHE_PREFIX_CODE, norm_phone)
            cd_key = cls._cache_key(cls.CACHE_PREFIX_COOLDOWN, norm_phone)
            attempts_key = cls._cache_key(cls.CACHE_PREFIX_ATTEMPTS, norm_phone)
            hashed_code = cls._hash_code(norm_phone, code)

            legacy_keys = (
                cls._legacy_cache_key(cls.CACHE_PREFIX_CODE, norm_phone),
                cls._legacy_cache_key(cls.CACHE_PREFIX_COOLDOWN, norm_phone),
                cls._legacy_cache_key(cls.CACHE_PREFIX_ATTEMPTS, norm_phone),
            )

            # Reserve a usable challenge before the external side effect. If
            # the cache/database write fails, no SMS request is made. The extra
            # provider timeout keeps the code/cooldown valid for the full user
            # window even when SMS.ir responds near its request timeout.
            reservation_seconds = cls.COOLDOWN_SECONDS + cls.TIMEOUT
            reservation_cooldown_until = time.time() + reservation_seconds
            with transaction.atomic():
                # No active legacy challenge remains (the cooldown check above
                # covered it). Remove any expired raw-phone entries before
                # reserving the new HMAC-addressed challenge.
                for legacy_key in legacy_keys:
                    cache.delete(legacy_key)

                cache.set(
                    code_key,
                    hashed_code,
                    timeout=cls.EXPIRATION_SECONDS + cls.TIMEOUT,
                )
                cache.set(
                    cd_key,
                    reservation_cooldown_until,
                    timeout=reservation_seconds,
                )
                cache.delete(attempts_key)

                # Django's DatabaseCache.set() can silently swallow a
                # DatabaseError raised by its internal UPDATE/INSERT and
                # return None. Read both values back in this transaction so
                # an incomplete reservation rolls back before any SMS side
                # effect can happen.
                stored_reservation = cache.get_many([code_key, cd_key])
                if (
                    stored_reservation.get(code_key) != hashed_code
                    or stored_reservation.get(cd_key)
                    != reservation_cooldown_until
                ):
                    raise DatabaseError(
                        "OTP cache reservation could not be verified."
                    )

            # A timeout/connection interruption does not prove that SMS.ir
            # rejected the request. Keep the reserved code and cooldown so a
            # delivered code remains usable and an immediate retry cannot send
            # a duplicate message.
            try:
                sent_ok, send_msg = cls.send_verification_code(norm_phone, code)
            except SMSDeliveryUncertain:
                remaining = cls.get_remaining_cooldown(norm_phone)
                return (
                    False,
                    "وضعیت ارسال پیامک مشخص نشد. اگر کد را دریافت کرده‌اید وارد کنید؛ "
                    "در غیر این صورت پس از پایان زمان اعلام‌شده دوباره درخواست دهید.",
                    remaining,
                )

            if not sent_ok:
                # A definitive provider rejection means no code was accepted.
                # Remove the reservation so the customer can retry immediately.
                with transaction.atomic():
                    cache.delete(code_key)
                    cache.delete(cd_key)
                    cache.delete(attempts_key)
                return False, send_msg, 0

            return (
                True,
                "کد تأیید ۶ رقمی با موفقیت ارسال شد.",
                cls.get_remaining_cooldown(norm_phone),
            )

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

            hashed_code_key = cls._cache_key(
                cls.CACHE_PREFIX_CODE, norm_phone
            )
            legacy_code_key = cls._legacy_cache_key(
                cls.CACHE_PREFIX_CODE, norm_phone
            )
            cached_hash = cache.get(hashed_code_key)
            if cached_hash is not None:
                code_key = hashed_code_key
                attempts_key = cls._cache_key(
                    cls.CACHE_PREFIX_ATTEMPTS, norm_phone
                )
            else:
                # A challenge created before the HMAC-key release remains
                # verifiable. Keep its attempt counter in the same namespace
                # until successful verification or natural expiry.
                code_key = legacy_code_key
                attempts_key = cls._legacy_cache_key(
                    cls.CACHE_PREFIX_ATTEMPTS, norm_phone
                )
                cached_hash = cache.get(code_key)

            if not cached_hash:
                return False, "کد تأیید منقضی شده یا هنوز درخواستی ثبت نشده است."

            try:
                attempts = int(cache.get(attempts_key, 0) or 0)
            except (TypeError, ValueError):
                attempts = 0

            if attempts >= cls.MAX_VERIFY_ATTEMPTS:
                return (
                    False,
                    "تعداد تلاش‌های ناموفق بیش از حد مجاز است. لطفاً کد جدید دریافت کنید.",
                )

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

            # Preserve the remaining legacy cooldown under its HMAC key before
            # removing any raw-phone cache keys. DatabaseCache writes are
            # checked because they may fail silently.
            cls._migrate_legacy_cooldown(norm_phone)

            # Remove both namespaces in case a rolling deployment left a
            # legacy entry beside the current HMAC-addressed challenge.
            cache.delete(code_key)
            cache.delete(attempts_key)
            cache.delete(legacy_code_key)
            cache.delete(
                cls._legacy_cache_key(cls.CACHE_PREFIX_ATTEMPTS, norm_phone)
            )
            return True, "کد تأیید با موفقیت احراز شد."
