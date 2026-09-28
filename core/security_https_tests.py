import runpy
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client, override_settings


SETTINGS_PATH = Path(__file__).resolve().parent / "settings.py"


def load_settings_env(**values):
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
    }
    env.update(overrides)
    return env


def test_production_enables_https_redirect():
    settings = load_settings_env(**production_base_env())

    assert settings["SECURE_SSL_REDIRECT"] is True


def test_development_does_not_force_https_redirect():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    assert settings["SECURE_SSL_REDIRECT"] is False


def test_production_trusts_forwarded_https_from_proxy():
    settings = load_settings_env(**production_base_env())

    assert settings["SECURE_PROXY_SSL_HEADER"] == (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )


def test_development_does_not_require_proxy_ssl_header():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    assert settings["SECURE_PROXY_SSL_HEADER"] is None


def test_production_hsts_contract():
    settings = load_settings_env(**production_base_env())

    assert settings["SECURE_HSTS_SECONDS"] == 31536000
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is True
    assert settings["SECURE_HSTS_PRELOAD"] is True


def test_development_hsts_is_disabled():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    assert settings["SECURE_HSTS_SECONDS"] == 0
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is False
    assert settings["SECURE_HSTS_PRELOAD"] is False


@pytest.mark.django_db
def test_https_redirect_happens_before_view_on_plain_http():
    with override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=(
            "HTTP_X_FORWARDED_PROTO",
            "https",
        ),
    ):
        response = Client().get("/accounts/login/")

    assert response.status_code == 301
    assert response["Location"] == "https://testserver/accounts/login/"


@pytest.mark.django_db
def test_forwarded_https_is_treated_as_secure_behind_proxy():
    with override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=(
            "HTTP_X_FORWARDED_PROTO",
            "https",
        ),
    ):
        response = Client().get(
            "/accounts/login/",
            HTTP_X_FORWARDED_PROTO="https",
        )

    assert response.status_code == 200


@pytest.mark.django_db
def test_hsts_header_is_sent_only_for_secure_requests():
    with override_settings(
        SECURE_SSL_REDIRECT=False,
        SECURE_PROXY_SSL_HEADER=(
            "HTTP_X_FORWARDED_PROTO",
            "https",
        ),
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=True,
    ):
        insecure = Client().get("/accounts/login/")
        secure = Client().get(
            "/accounts/login/",
            HTTP_X_FORWARDED_PROTO="https",
        )

    assert "Strict-Transport-Security" not in insecure
    assert (
        secure["Strict-Transport-Security"]
        == "max-age=31536000; includeSubDomains; preload"
    )
