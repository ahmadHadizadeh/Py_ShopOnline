from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("STOREFRONT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_ID = os.environ["STOREFRONT_SESSION_ID"]
ARTIFACTS = Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)

page_errors: list[str] = []


def assert_no_horizontal_overflow(page, width: int) -> None:
    dimensions = page.evaluate(
        "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
    )
    assert dimensions["document"] <= width, f"Document overflows viewport: {dimensions}"
    assert dimensions["body"] <= width, f"Body overflows viewport: {dimensions}"


def expect_visible(page, selector: str) -> None:
    locator = page.locator(selector)
    locator.wait_for(state="visible", timeout=3000)
    assert locator.is_visible(), f"Expected visible element: {selector}"


def expect_hidden(page, selector: str) -> None:
    locator = page.locator(selector)
    locator.wait_for(state="hidden", timeout=3000)
    assert not locator.is_visible(), f"Expected hidden element: {selector}"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()

    mobile_context = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=1,
    )
    mobile = mobile_context.new_page()
    mobile.on("pageerror", lambda error: page_errors.append(f"mobile: {error}"))

    response = mobile.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
    assert response is not None and response.status == 200, (
        f"Storefront home did not return HTTP 200: {None if response is None else response.status}"
    )
    expect_visible(mobile, "#storefront-header")
    expect_visible(mobile, "#mobile-nav-toggle")
    expect_hidden(mobile, "#desktop-search-form")
    expect_visible(mobile, "#mobile-search-toggle")
    assert_no_horizontal_overflow(mobile, 390)
    for width in (320, 360):
        mobile.set_viewport_size({"width": width, "height": 844})
        assert_no_horizontal_overflow(mobile, width)
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.screenshot(path=str(ARTIFACTS / "storefront-mobile-home.png"), full_page=True)

    # Mobile category drawer and its server-rendered active category link.
    mobile.locator("#mobile-nav-toggle").click()
    expect_visible(mobile, "#mobile-navigation-panel")
    assert mobile.locator("#mobile-navigation-panel").get_attribute("aria-hidden") == "false"
    category_link = mobile.locator(
        '#mobile-navigation-panel a[href="/category/browser-test-category/"]'
    )
    expect_visible(mobile, '#mobile-navigation-panel a[href="/category/browser-test-category/"]')
    category_link.click()
    mobile.wait_for_url("**/category/browser-test-category/")
    assert mobile.locator("#products-list h2").inner_text().strip() == "Browser Test"

    # Mobile search retains the current category and submits a real GET query.
    mobile.locator("#mobile-search-toggle").click()
    expect_visible(mobile, "#mobile-search-panel")
    mobile.locator("#mobile-search-input").fill("NorthwindBrowser")
    mobile.locator("#mobile-search-panel button[type='submit']").click()
    mobile.wait_for_url("**/category/browser-test-category/?q=NorthwindBrowser")
    assert "نتایج جست‌وجو" in mobile.locator("#products-list h2").inner_text()
    expect_visible(mobile, "#products-list")
    assert mobile.get_by_text("Browser Test Product", exact=True).count() >= 1
    assert_no_horizontal_overflow(mobile, 390)

    # Desktop header search remains visible and uses the same query contract.
    mobile.set_viewport_size({"width": 1440, "height": 900})
    response = mobile.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect_visible(mobile, "#desktop-search-form")
    expect_hidden(mobile, "#mobile-nav-toggle")
    mobile.locator("#desktop-search-input").fill("NorthwindBrowser")
    mobile.locator("#desktop-search-form button[type='submit']").click()
    mobile.wait_for_url("**/?q=NorthwindBrowser")
    assert "نتایج جست‌وجو" in mobile.locator("#products-list h2").inner_text()
    assert mobile.get_by_text("Browser Test Product", exact=True).count() >= 1
    assert_no_horizontal_overflow(mobile, 1440)
    mobile.screenshot(path=str(ARTIFACTS / "storefront-desktop-search.png"), full_page=True)

    # Product-detail scripts still see the stable global loader API.
    detail_url = f"{BASE_URL}/product/browser-test-product/"
    response = mobile.goto(detail_url, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert mobile.evaluate(
        "() => typeof window.showLoader === 'function' && typeof window.hideLoader === 'function'"
    ), "Product detail lost the global loader functions."
    assert mobile.evaluate(
        "() => typeof window.openOtpModal === 'function' && typeof window.handleVerifyOtp === 'function'"
    ), "OTP public API was not initialized before page interaction."

    # Authenticated header dropdown supports keyboard open and Escape close.
    authenticated_context = browser.new_context(
        viewport={"width": 1440, "height": 900},
    )
    authenticated_context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": SESSION_ID,
                "domain": "127.0.0.1",
                "path": "/",
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ]
    )
    stored_session_cookies = authenticated_context.cookies(BASE_URL)
    assert any(cookie["name"] == "sessionid" and cookie["value"] == SESSION_ID for cookie in stored_session_cookies), (
        "Playwright did not retain the Django session cookie."
    )
    authenticated = authenticated_context.new_page()
    authenticated.on("pageerror", lambda error: page_errors.append(f"authenticated: {error}"))
    response = authenticated.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    if authenticated.locator("#accountDropdown").count() != 1:
        cookie_names = [cookie["name"] for cookie in authenticated_context.cookies(BASE_URL)]
        body_excerpt = authenticated.locator("body").inner_text()[:500]
        raise AssertionError(
            "Django session cookie was not reflected in the rendered authenticated header. "
            f"Cookie names: {cookie_names}; URL: {authenticated.url}; "
            f"body excerpt: {body_excerpt!r}"
        )
    expect_visible(authenticated, "#accountDropdown")
    for width in (320, 360, 390, 640, 767, 768, 1024, 1440):
        authenticated.set_viewport_size({"width": width, "height": 844})
        assert_no_horizontal_overflow(authenticated, width)
    authenticated.set_viewport_size({"width": 390, "height": 844})
    authenticated.screenshot(
        path=str(ARTIFACTS / "storefront-authenticated-mobile.png"),
        full_page=True,
    )
    authenticated.set_viewport_size({"width": 1440, "height": 900})
    authenticated.locator("#accountDropdown").focus()
    authenticated.keyboard.press("Enter")
    assert authenticated.locator("#accountDropdown").get_attribute("aria-expanded") == "true"
    expect_visible(authenticated, "#accountMenu")
    authenticated.keyboard.press("Escape")
    assert authenticated.locator("#accountDropdown").get_attribute("aria-expanded") == "false"
    expect_hidden(authenticated, "#accountMenu")

    # Desktop category menu also opens by keyboard and closes by Escape.
    authenticated.locator("#desktop-categories-toggle").focus()
    authenticated.keyboard.press("Enter")
    assert authenticated.locator("#desktop-categories-toggle").get_attribute("aria-expanded") == "true"
    expect_visible(authenticated, "#desktop-categories-menu")
    authenticated.keyboard.press("Escape")
    assert authenticated.locator("#desktop-categories-toggle").get_attribute("aria-expanded") == "false"
    expect_hidden(authenticated, "#desktop-categories-menu")

    # Guest login keeps automatic OTP opening, manual trigger, and Escape behavior.
    guest_context = browser.new_context(viewport={"width": 390, "height": 844})
    guest = guest_context.new_page()
    guest.on("pageerror", lambda error: page_errors.append(f"guest: {error}"))
    response = guest.goto(f"{BASE_URL}/accounts/login/", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect_visible(guest, "#otp-modal")
    assert guest.locator("#otp-modal").get_attribute("aria-hidden") == "false"

    guest.locator('[data-otp-action="close"]').click()
    expect_hidden(guest, "#otp-modal")
    assert guest.locator("#otp-modal").get_attribute("aria-hidden") == "true"

    guest.locator("[data-otp-open]").click()
    expect_visible(guest, "#otp-modal")
    guest.keyboard.press("Escape")
    expect_hidden(guest, "#otp-modal")

    # Do not send SMS or verify a real OTP in this smoke test.
    assert not page_errors, "Browser JavaScript errors:\n" + "\n".join(page_errors)

    guest_context.close()
    authenticated_context.close()
    mobile_context.close()
    browser.close()

print("Storefront browser smoke passed: responsive header, category navigation, search, loader API, account menu, and OTP modal.")
