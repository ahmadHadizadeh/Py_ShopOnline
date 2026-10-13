import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest


SETTINGS_PATH = Path(__file__).resolve().parent / "settings.py"


def load_settings_env(**values):
    """Load the real settings module in an isolated environment."""
    with (
        patch.dict(os.environ, values, clear=True),
        patch("dotenv.load_dotenv"),
    ):
        return runpy.run_path(str(SETTINGS_PATH))


def production_base_env(**overrides):
    env = {
        "DJANGO_DEBUG": "0",
        "SECRET_KEY": "test-production-secret",
        "ALLOWED_HOSTS": "shop.example.com,admin.example.com",
        "CSRF_TRUSTED_ORIGINS": (
            "https://shop.example.com,https://admin.example.com"
        ),
        "PAYMENT_CALLBACK_SECRET": "test-payment-callback-secret",
        "PAYMENT_DEFAULT_GATEWAY": "mock_gateway",
        "SMSIR_API_KEY": "test-smsir-key",
        "DB_ENGINE": "postgresql",
        "DB_NAME": "pyshop_db",
        "DB_USER": "pyshop",
        "DB_PASSWORD": "test-db-password",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
    }
    env.update(overrides)
    return env


def zarinpal_production_env(**overrides):
    env = production_base_env(PAYMENT_DEFAULT_GATEWAY="zarinpal")
    env.update(
        {
            "ZARINPAL_MERCHANT_ID": "test-merchant",
            "ZARINPAL_CALLBACK_URL": (
                "https://shop.example.test/orders/payment/callback/"
            ),
            "ZARINPAL_REQUEST_URL": "https://gateway.example.test/request",
            "ZARINPAL_VERIFY_URL": "https://gateway.example.test/verify",
            "ZARINPAL_STARTPAY_URL": "https://gateway.example.test/startpay",
        }
    )
    env.update(overrides)
    return env


def test_debug_is_env_driven():
    settings = load_settings_env(
        **production_base_env(SECRET_KEY="test-secret"),
    )

    assert settings["DEBUG"] is False


def test_secret_key_comes_from_environment():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="env-secret",
    )

    assert settings["SECRET_KEY"] == "env-secret"


def test_production_requires_secret_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        load_settings_env(
            DJANGO_DEBUG="0",
            ALLOWED_HOSTS="example.com",
        )


def test_production_parses_allowed_hosts_from_environment():
    settings = load_settings_env(
        **production_base_env(
            ALLOWED_HOSTS="shop.example.com, admin.example.com",
        )
    )

    assert settings["ALLOWED_HOSTS"] == [
        "shop.example.com",
        "admin.example.com",
    ]


def test_production_requires_allowed_hosts():
    env = production_base_env()
    del env["ALLOWED_HOSTS"]

    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        load_settings_env(**env)


def test_production_does_not_add_development_tunnel_csrf_origin():
    settings = load_settings_env(**production_base_env())

    assert not any(
        "loca.lt" in origin
        for origin in settings["CSRF_TRUSTED_ORIGINS"]
    )


def test_development_keeps_local_defaults():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    assert settings["ALLOWED_HOSTS"] == [
        "127.0.0.1",
        "localhost",
        ".loca.lt",
    ]
    assert "https://*.loca.lt" in settings["CSRF_TRUSTED_ORIGINS"]
    assert settings["PAYMENT_DEFAULT_GATEWAY"] == "mock_gateway"
    assert settings["PAYMENT_CALLBACK_SECRET"] == "dev-secret"
    assert settings["ZARINPAL_REQUEST_URL"].startswith(
        "https://sandbox.zarinpal.com/"
    )


def test_production_requires_explicit_payment_callback_secret():
    env = production_base_env()
    del env["PAYMENT_CALLBACK_SECRET"]

    with pytest.raises(RuntimeError, match="PAYMENT_CALLBACK_SECRET"):
        load_settings_env(**env)


def test_production_does_not_fallback_to_mock_gateway():
    env = production_base_env()
    del env["PAYMENT_DEFAULT_GATEWAY"]

    with pytest.raises(RuntimeError, match="PAYMENT_DEFAULT_GATEWAY"):
        load_settings_env(**env)


def test_production_requires_smsir_api_key():
    env = production_base_env()
    del env["SMSIR_API_KEY"]

    with pytest.raises(RuntimeError, match="SMSIR_API_KEY"):
        load_settings_env(**env)


def test_zarinpal_production_requires_explicit_configuration():
    env = production_base_env(PAYMENT_DEFAULT_GATEWAY="zarinpal")

    with pytest.raises(RuntimeError, match="ZARINPAL_"):
        load_settings_env(**env)


def test_zarinpal_production_accepts_explicit_non_sandbox_configuration():
    settings = load_settings_env(**zarinpal_production_env())

    assert settings["ZARINPAL_MERCHANT_ID"] == "test-merchant"
    assert settings["ZARINPAL_CALLBACK_URL"] == (
        "https://shop.example.test/orders/payment/callback/"
    )
    assert settings["ZARINPAL_REQUEST_URL"] == (
        "https://gateway.example.test/request"
    )
    assert settings["ZARINPAL_VERIFY_URL"] == (
        "https://gateway.example.test/verify"
    )
    assert settings["ZARINPAL_STARTPAY_URL"] == (
        "https://gateway.example.test/startpay"
    )


def test_zarinpal_production_rejects_sandbox_endpoint():
    with pytest.raises(RuntimeError, match="sandbox"):
        load_settings_env(
            **zarinpal_production_env(
                ZARINPAL_REQUEST_URL=(
                    "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
                )
            )
        )


def test_production_uses_secure_session_and_csrf_cookies():
    settings = load_settings_env(**production_base_env())

    assert settings["SESSION_COOKIE_SECURE"] is True
    assert settings["CSRF_COOKIE_SECURE"] is True


def test_development_does_not_force_secure_cookies():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    assert settings["SESSION_COOKIE_SECURE"] is False
    assert settings["CSRF_COOKIE_SECURE"] is False


def test_session_cookie_security_contract():
    settings = load_settings_env(**production_base_env())

    assert settings["SESSION_COOKIE_HTTPONLY"] is True
    assert settings["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_csrf_cookie_remains_javascript_readable():
    settings = load_settings_env(**production_base_env())

    assert settings["CSRF_COOKIE_HTTPONLY"] is False
    assert settings["CSRF_COOKIE_SAMESITE"] == "Lax"


def test_production_uses_postgresql_from_environment():
    settings = load_settings_env(
        **production_base_env(
            DB_ENGINE="postgresql",
            DB_NAME="pyshop_db",
            DB_USER="pyshop",
            DB_PASSWORD="test-db-password",
            DB_HOST="127.0.0.1",
            DB_PORT="5432",
        )
    )

    database = settings["DATABASES"]["default"]

    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["NAME"] == "pyshop_db"
    assert database["USER"] == "pyshop"
    assert database["PASSWORD"] == "test-db-password"
    assert database["HOST"] == "127.0.0.1"
    assert database["PORT"] == "5432"


def test_production_uses_real_test_database_default():
    settings = load_settings_env(
        **production_base_env(
            DB_ENGINE="postgresql",
        )
    )

    assert settings["DATABASES"]["default"]["TEST"]["NAME"] == (
        "pyshop_test_db"
    )


def test_production_accepts_explicit_test_database_name():
    settings = load_settings_env(
        **production_base_env(
            DB_ENGINE="postgresql",
            DB_TEST_NAME="custom_pyshop_test_db",
        )
    )

    assert settings["DATABASES"]["default"]["TEST"]["NAME"] == (
        "custom_pyshop_test_db"
    )


def test_development_keeps_sqlite_database_default():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    database = settings["DATABASES"]["default"]

    assert database["ENGINE"] == "django.db.backends.sqlite3"
    assert database["NAME"] == (
        Path(settings["BASE_DIR"]) / "db.sqlite3"
    )


@pytest.mark.parametrize(
    "missing_variable",
    [
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
    ],
)
def test_production_requires_database_configuration(missing_variable):
    env = production_base_env()
    del env[missing_variable]

    with pytest.raises(RuntimeError, match=missing_variable):
        load_settings_env(**env)


def test_rejects_unsupported_database_engine():
    with pytest.raises(RuntimeError, match="Unsupported DB_ENGINE"):
        load_settings_env(
            **production_base_env(
                DB_ENGINE="mysql",
            )
        )
