import pytest
from django.conf import settings
from django.test import Client


REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"

REQUIRED_SCRIPT_SOURCES = {
    "'self'",
    "'unsafe-inline'",
    "https://cdn.tailwindcss.com",
    "https://cdnjs.cloudflare.com",
    "https://code.jquery.com",
    "https://cdn.jsdelivr.net",
}

REQUIRED_STYLE_SOURCES = {
    "'self'",
    "'unsafe-inline'",
    "https://cdnjs.cloudflare.com",
    "https://cdn.jsdelivr.net",
}


def _csp_directive(policy, name):
    for directive in policy.split(";"):
        directive = directive.strip()
        if directive.startswith(f"{name} "):
            return set(directive.split()[1:])
    return set()


def _report_only_policy():
    policy = getattr(settings, "SECURE_CSP_REPORT_ONLY", None)
    assert policy is not None, (
        "3.7.4 contract: SECURE_CSP_REPORT_ONLY must be configured "
        "before CSP enforcement is considered."
    )
    assert isinstance(policy, str), (
        "3.7.4 contract: SECURE_CSP_REPORT_ONLY must use Django 6 "
        "string CSP syntax."
    )
    return policy


@pytest.mark.django_db
def test_csp_report_only_header_is_emitted_on_a_real_html_response():
    client = Client(HTTP_HOST="localhost")
    response = client.get("/admin/login/")

    assert response.status_code == 200
    assert REPORT_ONLY_HEADER in response
    assert response[REPORT_ONLY_HEADER].strip()


@pytest.mark.django_db
def test_csp_report_only_policy_contains_sources_used_by_current_frontend():
    policy = _report_only_policy()

    script_sources = _csp_directive(policy, "script-src")
    style_sources = _csp_directive(policy, "style-src")

    assert REQUIRED_SCRIPT_SOURCES <= script_sources
    assert REQUIRED_STYLE_SOURCES <= style_sources


def test_csp_report_only_contract_does_not_enable_enforcement_yet():
    assert getattr(settings, "SECURE_CSP", None) in (None, {}), (
        "3.7.4 contract: CSP enforcement is intentionally deferred until "
        "Report-Only violations are audited against the real frontend."
    )


def test_csp_report_only_policy_has_minimum_baseline_directives():
    policy = _report_only_policy()

    assert "'none'" in " ".join(
        token for token in policy.split() if token == "'none'"
    ) or "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    assert "frame-ancestors 'none'" in policy
