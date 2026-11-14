import hashlib
import hmac
import re

from django.conf import settings
from django.core.cache import caches
from django.db import migrations


CACHE_TABLE = "pyshop_cache"
CACHE_KEY_HMAC_DOMAIN = "pyshop:otp-cache:v1"
OTP_KEY_PATTERN = re.compile(
    r"(otp_code|otp_cooldown|otp_attempts):(09[0-9]{9})$"
)


def rekey_legacy_otp_cache_entries(apps, schema_editor):
    """
    Replace raw-phone OTP cache-key suffixes with SECRET_KEY-based HMACs.

    Only cache_key is changed. The serialized value and expires timestamp are
    preserved byte-for-byte, so an in-flight OTP challenge remains valid.
    """
    connection = schema_editor.connection
    if CACHE_TABLE not in connection.introspection.table_names():
        # Fresh installations may not have run createcachetable yet.
        return

    cache_config = settings.CACHES.get("default", {})
    if cache_config.get("BACKEND") != "django.core.cache.backends.db.DatabaseCache":
        return

    secret = str(settings.SECRET_KEY).encode("utf-8")
    backend = caches["default"]
    table = connection.ops.quote_name(CACHE_TABLE)

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT cache_key FROM {table} "
            "WHERE cache_key LIKE %s "
            "OR cache_key LIKE %s "
            "OR cache_key LIKE %s",
            ["%otp_code:%", "%otp_cooldown:%", "%otp_attempts:%"],
        )
        legacy_keys = [row[0] for row in cursor.fetchall()]

        for legacy_storage_key in legacy_keys:
            match = OTP_KEY_PATTERN.search(legacy_storage_key)
            if match is None:
                continue

            prefix, phone = match.groups()
            message = f"{CACHE_KEY_HMAC_DOMAIN}:{phone}".encode("utf-8")
            identifier = hmac.new(
                secret,
                message,
                hashlib.sha256,
            ).hexdigest()
            logical_key = f"{prefix}:{identifier}"
            new_storage_key = backend.make_key(logical_key)

            if new_storage_key == legacy_storage_key:
                continue

            cursor.execute(
                f"SELECT 1 FROM {table} WHERE cache_key = %s",
                [new_storage_key],
            )
            if cursor.fetchone() is not None:
                raise RuntimeError(
                    "Refusing to overwrite an existing HMAC OTP cache entry."
                )

            cursor.execute(
                f"UPDATE {table} SET cache_key = %s WHERE cache_key = %s",
                [new_storage_key, legacy_storage_key],
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Legacy OTP cache entry changed during HMAC migration."
                )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_alter_profile_phone_number"),
    ]

    operations = [
        migrations.RunPython(rekey_legacy_otp_cache_entries),
    ]
