import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest


SETTINGS_PATH = Path(__file__).resolve().parent / "settings.py"


def load_settings_env(**values):
    """Load the real settings module in an isolated environment."""
    with patch.dict(os.environ, values, clear=True):
        return runpy.run_path(str(SETTINGS_PATH))


def production_base_env(**overrides):
    env = {
        "DJANGO_DEBUG": "0",
        "SECRET_KEY": "test-production-secret",
        "ALLOWED_HOSTS": "shop.example.com,admin.example.com",
        "CSRF_TRUSTED_ORIGINS": "https://shop.example.com,https://admin.example.com",
        "PAYMENT_CALLBACK_SECRET": "test-payment-callback-secret",
        "PAYMENT_DEFAULT_GATEWAY": "mock_gateway",
        "SMSIR_API_KEY": "test-smsir-key",
    }
    env.update(overrides)
    return env


def test_debug_is_env_driven():
    settings = load_settings_env(
        DJANGO_DEBUG="0",
        SECRET_KEY="test-secret",
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


def test_production_does_not_add_development_tunnel_csrf_origin():
    settings = load_settings_env(**production_base_env())

    assert not any(
        "loca.lt" in origin
        for origin in settings["CSRF_TRUSTED_ORIGINS"]
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


def test_zarinpal_production_configuration_has_no_sandbox_fallback():
    env = production_base_env(PAYMENT_DEFAULT_GATEWAY="zarinpal")

    with pytest.raises(RuntimeError, match="ZARINPAL_"):
        load_settings_env(**env)
