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


def assert_footer_layout(page, width: int) -> None:
    grid = page.locator("#storefront-footer-grid")
    columns = grid.evaluate(
        "(element) => getComputedStyle(element).gridTemplateColumns.split(' ').filter(Boolean).length"
    )
    expected_columns = 4 if width >= 768 else 1
    assert columns == expected_columns, (
        f"Unexpected footer columns at {width}px: {columns}"
    )

    image = page.locator("#storefront-enamad-placeholder img")
    image_width = image.evaluate(
        "(element) => element.getBoundingClientRect().width"
    )
    expected_image_width = 125 if width >= 768 else 112 if width >= 640 else 96
    assert abs(image_width - expected_image_width) <= 1, (
        f"Unexpected responsive placeholder width at {width}px: {image_width}"
    )

    grid_box = grid.bounding_box()
    image_box = image.bounding_box()
    assert grid_box is not None and image_box is not None
    assert image_box["x"] >= grid_box["x"] - 1
    assert image_box["x"] + image_box["width"] <= grid_box["x"] + grid_box["width"] + 1

    if width >= 768:
        positions = page.evaluate(
            """() => {
                const rect = (selector) => {
                    const element = document.querySelector(selector);
                    const box = element.getBoundingClientRect();
                    return {left: box.left, right: box.right};
                };
                return {
                    brand: rect("#storefront-footer-brand"),
                    links: rect("#storefront-footer-links"),
                    placeholder: rect("#storefront-enamad-placeholder"),
                };
            }"""
        )
        assert positions["placeholder"]["right"] < positions["links"]["left"], (
            "The placeholder should occupy the visual left side of the RTL footer."
        )
        assert positions["links"]["right"] < positions["brand"]["left"], (
            "The quick links should sit between the brand and left-side placeholder."
        )


def assert_hero_heading_spacing(page, width: int) -> None:
    heading = page.locator("#storefront-hero-heading")
    assert heading.locator('[data-storefront-hero-line="1"]').count() == 1
    assert heading.locator('[data-storefront-hero-line="2"]').count() == 1
    actual_gap = float(
        heading.evaluate("(element) => getComputedStyle(element).rowGap").replace("px", "")
    )
    expected_gap = 16 if width >= 640 else 12
    assert abs(actual_gap - expected_gap) <= 1, (
        f"Unexpected hero heading line gap at {width}px: {actual_gap}px"
    )


def expect_visible(page, selector: str) -> None:
    locator = page.locator(selector)
    locator.wait_for(state="visible", timeout=3000)
    assert locator.is_visible(), f"Expected visible element: {selector}"


def expect_hidden(page, selector: str) -> None:
    locator = page.locator(selector)
    locator.wait_for(state="hidden", timeout=3000)
    assert not locator.is_visible(), f"Expected hidden element: {selector}"


def assert_checkout_non_address_state(
    page,
    shipping_method_id: str,
    customer_note: str,
) -> None:
    shipping_input = page.locator("#selected_shipping_method_id")
    assert shipping_input.input_value() == shipping_method_id

    active_shipping = page.locator(".shipping-method-card.active-shipping")
    assert active_shipping.count() == 1
    assert active_shipping.get_attribute("data-id") == shipping_method_id

    assert page.locator("#id_customer_note").input_value() == customer_note


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
    expect_visible(mobile, "#storefront-hero")
    expect_visible(mobile, "#storefront-hero-heading [data-storefront-hero-line='1']")
    expect_visible(mobile, "#storefront-hero-heading [data-storefront-hero-line='2']")
    assert_hero_heading_spacing(mobile, 390)
    expect_visible(mobile, "#storefront-trust-strip")
    assert mobile.locator("#storefront-trust-strip h2").count() == 4
    expect_visible(mobile, "#mobile-nav-toggle")
    expect_hidden(mobile, "#desktop-search-form")
    expect_visible(mobile, "#mobile-search-toggle")
    assert_no_horizontal_overflow(mobile, 390)
    assert mobile.locator("#product-grid").get_attribute("data-total-count") == "13"

    enamad_section = mobile.locator("#storefront-enamad-placeholder")
    expect_visible(mobile, "#storefront-enamad-placeholder")
    enamad_image = enamad_section.locator("img")
    assert enamad_image.get_attribute("alt") == (
        "تصویر جایگزین نماد اعتماد؛ نشان‌دهنده تأیید رسمی فروشگاه نیست"
    )
    assert enamad_image.get_attribute("width") == "125"
    assert enamad_image.get_attribute("height") == "136"
    assert enamad_image.get_attribute("loading") == "lazy"
    assert enamad_image.get_attribute("decoding") == "async"
    assert enamad_image.get_attribute("src") == (
        "/static/images/storefront/enamad-placeholder.png"
    )
    assert mobile.locator("#storefront-enamad-notice").inner_text().strip() == (
        "این تصویر موقت است و به‌معنای تأیید رسمی فروشگاه نیست."
    )

    image_response = mobile.request.get(
        f"{BASE_URL}/static/images/storefront/enamad-placeholder.png"
    )
    assert image_response.status == 200, (
        f"Placeholder image returned HTTP {image_response.status}"
    )
    assert image_response.headers.get("content-type", "").split(";")[0] == "image/png"
    image_bytes = image_response.body()
    assert 0 < len(image_bytes) <= 4096, (
        f"Placeholder image is unexpectedly large: {len(image_bytes)} bytes"
    )

    enamad_image.scroll_into_view_if_needed()
    mobile.wait_for_function(
        """() => {
            const image = document.querySelector("#storefront-enamad-placeholder img");
            return image && image.complete
                && image.naturalWidth === 125
                && image.naturalHeight === 136;
        }""",
        timeout=5000,
    )
    for width, expected_columns in ((320, 1), (360, 1), (390, 1), (640, 2), (768, 2), (1024, 3), (1280, 4), (1440, 4)):
        mobile.set_viewport_size({"width": width, "height": 844})
        assert_no_horizontal_overflow(mobile, width)
        assert_footer_layout(mobile, width)
        assert_hero_heading_spacing(mobile, width)
        category_columns = mobile.locator(
            '#products-list nav[aria-label="دسته‌بندی‌های فروشگاه"]'
        ).evaluate(
            "(element) => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        expected_category_columns = 2 if width < 640 else 3 if width < 1024 else 4
        assert category_columns == expected_category_columns, (
            f"Unexpected category grid columns at {width}px: {category_columns}"
        )
        trust_columns = mobile.locator("#storefront-trust-strip").evaluate(
            "(element) => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        expected_trust_columns = 4 if width >= 1024 else 2
        assert trust_columns == expected_trust_columns, (
            f"Unexpected trust-strip columns at {width}px: {trust_columns}"
        )
        columns = mobile.locator("#product-grid").evaluate(
            "(element) => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert columns == expected_columns, (
            f"Unexpected product grid columns at {width}px: {columns}"
        )
        card_direction = mobile.locator("#product-grid article").first.evaluate(
            "(card) => getComputedStyle(card).flexDirection"
        )
        expected_direction = "row" if width < 640 else "column"
        assert card_direction == expected_direction, (
            f"Unexpected product card direction at {width}px: {card_direction}"
        )
        if width < 640:
            image_frame_width = mobile.locator("#product-grid article").first.locator(
                ":scope > div"
            ).first.evaluate("(frame) => frame.getBoundingClientRect().width")
            assert image_frame_width <= 112.5, (
                f"Mobile product image frame is not compact at {width}px: {image_frame_width}"
            )
            mobile_card_transform = mobile.locator("#product-grid article").first.evaluate(
                "(card) => getComputedStyle(card).transform"
            )
            assert mobile_card_transform == "none", (
                f"Product card should not depend on hover at {width}px: {mobile_card_transform}"
            )
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.emulate_media(reduced_motion="reduce")
    reduced_motion_transition = mobile.locator("#product-grid article").first.evaluate(
        "(card) => getComputedStyle(card).transitionProperty"
    )
    assert reduced_motion_transition == "none", (
        f"Product card ignored prefers-reduced-motion: {reduced_motion_transition}"
    )
    mobile.emulate_media(reduced_motion="no-preference")
    mobile.screenshot(path=str(ARTIFACTS / "storefront-mobile-home.png"), full_page=True)

    # Touch-capable tablets must not receive hover-only card treatments merely
    # because their CSS viewport is wider than the mobile breakpoint.
    touch_context = browser.new_context(
        viewport={"width": 768, "height": 844},
        device_scale_factor=1,
        is_mobile=True,
        has_touch=True,
    )
    touch_page = touch_context.new_page()
    touch_page.on("pageerror", lambda error: page_errors.append(f"touch: {error}"))
    touch_response = touch_page.goto(
        f"{BASE_URL}/",
        wait_until="domcontentloaded",
    )
    assert touch_response is not None and touch_response.status == 200
    assert touch_page.evaluate(
        "() => window.matchMedia('(hover: none)').matches"
    ), "Playwright touch context did not expose a non-hover pointer."
    assert_no_horizontal_overflow(touch_page, 768)
    touch_card = touch_page.locator("#product-grid article").first
    touch_card.hover()
    touch_card_transform = touch_card.evaluate(
        "(card) => getComputedStyle(card).transform"
    )
    assert touch_card_transform == "none", (
        "A touch-capable tablet received the product-card hover transform: "
        f"{touch_card_transform}"
    )
    touch_category_card = touch_page.locator(
        '#products-list nav[aria-label="دسته‌بندی‌های فروشگاه"] .category-card'
    ).first
    touch_category_card.hover()
    touch_category_transform = touch_category_card.evaluate(
        "(card) => getComputedStyle(card).transform"
    )
    assert touch_category_transform == "none", (
        "A touch-capable tablet received the category-card hover transform: "
        f"{touch_category_transform}"
    )

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
    expect_hidden(mobile, "#storefront-hero")
    expect_hidden(mobile, "#storefront-trust-strip")
    assert mobile.locator("#products-list h2").inner_text().strip() == "Browser Test"

    # Mobile search retains the current category and submits a real GET query.
    mobile.locator("#mobile-search-toggle").click()
    expect_visible(mobile, "#mobile-search-panel")
    mobile.locator("#mobile-search-input").fill("NorthwindBrowser")
    expect_visible(mobile, "#mobile-search-suggestions")
    mobile_suggestions = mobile.locator("#mobile-search-suggestions a[role='option']")
    assert 0 < mobile_suggestions.count() <= 6
    assert mobile_suggestions.first.get_attribute("href").startswith("/product/")
    mobile.locator("#mobile-search-panel button[type='submit']").click()
    mobile.wait_for_url("**/category/browser-test-category/?q=NorthwindBrowser")
    assert "نتایج جست‌وجو" in mobile.locator("#products-list h2").inner_text()
    expect_visible(mobile, "#products-list")
    assert mobile.locator("#product-grid").get_attribute("data-total-count") == "13"
    assert mobile.locator("#product-grid article").count() == 12
    assert_no_horizontal_overflow(mobile, 390)

    # Sorting is a GET form and keeps the active query/category.
    mobile.locator("#product-sort").select_option("price_asc")
    mobile.locator("#product-sort-apply").click()
    mobile.wait_for_url("**/category/browser-test-category/?q=NorthwindBrowser&sort=price_asc")
    ascending_prices = mobile.locator("#product-grid article").evaluate_all(
        "(cards) => cards.map((card) => Number(card.dataset.productPrice))"
    )
    assert ascending_prices == sorted(ascending_prices), (
        f"Price ascending order is incorrect: {ascending_prices}"
    )
    assert mobile.locator("#pagination-next").is_visible()
    mobile.locator("#pagination-next").click()
    mobile.wait_for_url("**/category/browser-test-category/?q=NorthwindBrowser&sort=price_asc&page=2")
    assert mobile.locator("#product-grid").get_attribute("data-total-count") == "13"
    assert mobile.locator("#product-grid article").count() == 1
    page_indicator = mobile.locator("#pagination-current").inner_text()
    assert "2" in page_indicator or "۲" in page_indicator

    # Desktop header search remains visible and uses the same query contract.
    mobile.set_viewport_size({"width": 1440, "height": 900})
    response = mobile.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect_visible(mobile, "#desktop-search-form")
    expect_visible(mobile, "#storefront-hero")
    expect_visible(mobile, "#storefront-trust-strip")
    expect_hidden(mobile, "#mobile-nav-toggle")
    assert mobile.locator("#product-grid").get_attribute("data-total-count") == "13"
    assert_footer_layout(mobile, 1440)
    mobile.screenshot(path=str(ARTIFACTS / "storefront-desktop-home.png"), full_page=True)
    desktop_card = mobile.locator("#product-grid article").first
    desktop_card.hover()
    desktop_card_transform = desktop_card.evaluate(
        "(card) => getComputedStyle(card).transform"
    )
    assert desktop_card_transform != "none", (
        "Desktop product card hover treatment did not activate."
    )
    mobile.emulate_media(reduced_motion="reduce")
    reduced_desktop_transform = desktop_card.evaluate(
        "(card) => getComputedStyle(card).transform"
    )
    reduced_desktop_transition = desktop_card.evaluate(
        "(card) => getComputedStyle(card).transitionProperty"
    )
    assert reduced_desktop_transform == "none", (
        "Product card still moves on hover when reduced motion is requested: "
        f"{reduced_desktop_transform}"
    )
    assert reduced_desktop_transition == "none", (
        "Product card still transitions on hover when reduced motion is requested: "
        f"{reduced_desktop_transition}"
    )
    mobile.emulate_media(reduced_motion="no-preference")
    mobile.mouse.move(0, 0)
    mobile.locator("#desktop-search-input").fill("NorthwindBrowser")
    expect_visible(mobile, "#desktop-search-suggestions")
    desktop_suggestions = mobile.locator("#desktop-search-suggestions a[role='option']")
    assert 0 < desktop_suggestions.count() <= 6
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

    assert mobile.locator('script[src$="/static/js/storefront/details.js"]').count() == 1
    assert mobile.locator("script:not([src])").count() == 0, "Product detail must not contain inline scripts."
    assert mobile.locator("[onclick]").count() == 0
    assert mobile.locator("#review-form").count() == 0

    # Guest page controls must work even though no review form is rendered.
    quantity_input = mobile.locator('input[name="quantity"]')
    assert quantity_input.input_value() == "1"
    mobile.locator('[data-quantity-action="increase"]').click()
    assert quantity_input.input_value() == "2"
    mobile.locator('[data-quantity-action="decrease"]').click()
    assert quantity_input.input_value() == "1"

    old_price_display = "#product-old-price-display"
    discount_badge = "#product-discount-badge"
    expect_hidden(mobile, old_price_display)
    expect_hidden(mobile, discount_badge)

    discounted_variant = mobile.locator(
        '#product-variant option[data-price-adjustment="-350.00"]'
    )
    assert discounted_variant.count() == 1
    mobile.locator("#product-variant").select_option(
        discounted_variant.get_attribute("value")
    )
    expected_discounted_price = mobile.evaluate(
        "() => new Intl.NumberFormat('fa-IR', {maximumFractionDigits: 0}).format(1750)"
    )
    assert mobile.locator("#product-price-display").inner_text() == expected_discounted_price
    expect_visible(mobile, old_price_display)
    expect_visible(mobile, discount_badge)

    non_discounted_variant = mobile.locator(
        '#product-variant option[data-price-adjustment="250.00"]'
    )
    assert non_discounted_variant.count() == 1
    mobile.locator("#product-variant").select_option(
        non_discounted_variant.get_attribute("value")
    )
    expected_non_discounted_price = mobile.evaluate(
        "() => new Intl.NumberFormat('fa-IR', {maximumFractionDigits: 0}).format(2350)"
    )
    assert mobile.locator("#product-price-display").inner_text() == expected_non_discounted_price
    expect_hidden(mobile, old_price_display)
    expect_hidden(mobile, discount_badge)

    # Switching back must restore the discount using the newly selected final price.
    mobile.locator("#product-variant").select_option(
        discounted_variant.get_attribute("value")
    )
    assert mobile.locator("#product-price-display").inner_text() == expected_discounted_price
    expect_visible(mobile, old_price_display)
    expect_visible(mobile, discount_badge)

    mobile.locator("#open-image-modal").click()
    expect_visible(mobile, "#image-modal")
    mobile.locator("#zoom-in").click()
    assert mobile.locator("#modal-product-image").evaluate(
        "(image) => image.style.transform"
    ) == "scale(1.25)"
    mobile.keyboard.press("Escape")
    expect_hidden(mobile, "#image-modal")

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

    # Exercise the real authenticated Checkout page without submitting the form.
    checkout_response = authenticated.goto(
        f"{BASE_URL}/cart/checkout/",
        wait_until="domcontentloaded",
    )
    assert checkout_response is not None and checkout_response.status == 200, (
        "Authenticated Checkout did not return HTTP 200: "
        f"{None if checkout_response is None else checkout_response.status}"
    )

    default_address_card = authenticated.locator(
        '.address-card[data-is-default="true"]'
    )
    secondary_address_card = authenticated.locator(
        '.address-card[data-is-default="false"]'
    )
    assert default_address_card.count() == 1
    assert secondary_address_card.count() == 1

    default_address_id = default_address_card.get_attribute("data-id")
    assert default_address_id
    assert authenticated.locator("#selected_address_id").input_value() == default_address_id
    assert authenticated.locator("#id_is_default").is_checked()
    assert authenticated.locator('input[name="recipient_name"]').input_value() == (
        "گیرنده پیش‌فرض مرورگر"
    )
    assert authenticated.locator('input[name="province"]').input_value() == "تهران"
    assert authenticated.locator('select[name="city"]').input_value() == "تهران"

    shipping_card = authenticated.locator(".shipping-method-card").first
    assert shipping_card.count() == 1
    shipping_card.click()
    shipping_method_id = shipping_card.get_attribute("data-id")
    assert shipping_method_id

    customer_note = "یادداشت مرورگر برای آزمون Checkout"
    authenticated.locator("#id_customer_note").fill(customer_note)

    # Starting a new address must clear only address state.
    authenticated.locator("#new-address-button").click()
    assert authenticated.locator("#selected_address_id").input_value() == ""
    assert not authenticated.locator("#id_is_default").is_checked()
    for field_name in ("recipient_name", "phone_number", "postal_code", "address_line"):
        assert authenticated.locator(
            f'#checkoutForm [name="{field_name}"]'
        ).input_value() == "", f"New address did not clear {field_name}."
    assert authenticated.locator('select[name="province"]').input_value() == ""
    assert authenticated.locator('select[name="city"]').input_value() == ""
    assert_checkout_non_address_state(
        authenticated,
        shipping_method_id,
        customer_note,
    )

    # Selecting a non-default saved address must keep its checkbox unchecked.
    secondary_address_card.click()
    assert authenticated.locator("#selected_address_id").input_value() == (
        secondary_address_card.get_attribute("data-id")
    )
    assert not authenticated.locator("#id_is_default").is_checked()
    assert authenticated.locator('input[name="recipient_name"]').input_value() == (
        "گیرنده دوم مرورگر"
    )
    assert_checkout_non_address_state(
        authenticated,
        shipping_method_id,
        customer_note,
    )

    # Re-selecting the default address must restore the checked state.
    default_address_card.click()
    assert authenticated.locator("#selected_address_id").input_value() == default_address_id
    assert authenticated.locator("#id_is_default").is_checked()
    assert authenticated.locator('input[name="recipient_name"]').input_value() == (
        "گیرنده پیش‌فرض مرورگر"
    )
    assert_checkout_non_address_state(
        authenticated,
        shipping_method_id,
        customer_note,
    )
    authenticated.screenshot(
        path=str(ARTIFACTS / "storefront-checkout-default-address-mobile.png"),
        full_page=True,
    )

    # Starting a new address again after selecting the default must still reset it.
    authenticated.locator("#new-address-button").click()
    assert authenticated.locator("#selected_address_id").input_value() == ""
    assert not authenticated.locator("#id_is_default").is_checked()
    assert_checkout_non_address_state(
        authenticated,
        shipping_method_id,
        customer_note,
    )
    assert_no_horizontal_overflow(authenticated, 390)
    authenticated.screenshot(
        path=str(ARTIFACTS / "storefront-checkout-new-address-mobile.png"),
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

    # Authenticated product detail still supports wishlist and review actions.
    detail_response = authenticated.goto(detail_url, wait_until="domcontentloaded")
    assert detail_response is not None and detail_response.status == 200
    expect_visible(authenticated, "#review-form")
    authenticated.locator('.rating-button[data-rating="3"]').click()
    assert authenticated.locator("#rating-input").input_value() == "3"

    authenticated.locator("#wishlist-btn").click()
    authenticated.wait_for_function(
        "() => document.querySelector('#wishlist-message')?.textContent.includes('محصول به علاقه مندی ها اضافه شد.')"
    )
    assert "محصول به علاقه مندی ها اضافه شد." in authenticated.locator("#wishlist-message").inner_text()

    authenticated.locator("#review-comment").fill("تجربه مرورگر از صفحه جزئیات محصول")
    authenticated.locator('#review-form button[type="submit"]').click()
    authenticated.wait_for_function(
        "() => document.querySelector('#review-message')?.textContent.includes('نظر شما با موفقیت ثبت شد.')"
    )
    assert "نظر شما با موفقیت ثبت شد." in authenticated.locator("#review-message").inner_text()

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
    touch_context.close()
    mobile_context.close()
    browser.close()

print("Storefront browser smoke passed: responsive storefront, guest detail controls, authenticated review/wishlist, and OTP modal.")
