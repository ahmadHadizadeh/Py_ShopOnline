import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client, override_settings


SETTINGS_PATH = Path(__file__).resolve().parent / "settings.py"


def load_settings_env(**values):
    """Load the real settings module in an isolated environment."""
    with (
        patch.dict(__import__("os").environ, values, clear=True),
        patch("dotenv.load_dotenv"),
    ):
        return runpy.run_path(str(SETTINGS_PATH))


def production_base_env(**overrides):
    env = {
        "DJANGO_DEBUG": "0",
        "SECRET_KEY": "test-production-secret",
        "ALLOWED_HOSTS": "shop.example.com",
        "CSRF_TRUSTED_ORIGINS": "https://shop.example.com",
        "PAYMENT_CALLBACK_SECRET": "test-payment-callback-secret",
        "PAYMENT_DEFAULT_GATEWAY": "mock_gateway",
        "SMSIR_API_KEY": "test-smsir-key",
    }
    env.update(overrides)
    return env


def test_development_https_security_defaults_are_safe_for_local_http():
    settings = load_settings_env(
        DJANGO_DEBUG="1",
        SECRET_KEY="dev-secret",
    )

    assert settings["SECURE_SSL_REDIRECT"] is False
    assert settings["SECURE_PROXY_SSL_HEADER"] is None
    assert settings["SECURE_HSTS_SECONDS"] == 0
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is False
    assert settings["SECURE_HSTS_PRELOAD"] is False


def test_production_requires_https_security_configuration():
    settings = load_settings_env(**production_base_env())

    assert settings["SECURE_SSL_REDIRECT"] is True
    assert settings["SECURE_PROXY_SSL_HEADER"] == (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )
    assert settings["SECURE_HSTS_SECONDS"] == 3600
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is False
    assert settings["SECURE_HSTS_PRELOAD"] is False


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
def test_http_request_redirects_to_https_under_future_production_proxy_contract():
    response = Client().get("/")

    assert response.status_code == 301
    assert response["Location"] == "https://testserver/"


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
def test_forwarded_https_request_is_not_redirected_by_django():
    response = Client().get(
        "/",
        HTTP_X_FORWARDED_PROTO="https",
    )

    assert response.status_code != 301


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_HSTS_SECONDS=3600,
    SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
    SECURE_HSTS_PRELOAD=False,
)
def test_forwarded_https_response_emits_staged_hsts_header():
    response = Client().get(
        "/",
        HTTP_X_FORWARDED_PROTO="https",
    )

    assert response["Strict-Transport-Security"] == "max-age=3600"
    assert "includeSubDomains" not in response["Strict-Transport-Security"]
    assert "preload" not in response["Strict-Transport-Security"]


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
def test_untrusted_forwarded_protocol_does_not_mark_request_secure():
    response = Client().get(
        "/",
        HTTP_X_FORWARDED_PROTO="http",
    )

    assert response.status_code == 301
    assert response["Location"] == "https://testserver/"
