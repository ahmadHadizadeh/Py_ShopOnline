from __future__ import annotations

import json
import os
from urllib.parse import urlsplit
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
BASE_URL = "http://127.0.0.1:18765"
USERNAME = "browser-smoke-admin"
PASSWORD = "BrowserSmokeOnly_2026!"

os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"
os.environ["DJANGO_DEBUG"] = "1"
os.environ["DB_ENGINE"] = "sqlite3"
os.environ["PAYMENT_DEFAULT_GATEWAY"] = "mock_gateway"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from catalog.models.category import Category  # noqa: E402
from catalog.models.product import Product  # noqa: E402
from orders.models import Order, Payment  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402


def prepare_database() -> None:
    call_command("migrate", interactive=False, verbosity=0)
    user_model = get_user_model()
    admin_user, created = user_model.objects.get_or_create(
        username=USERNAME,
        defaults={"email": "browser-smoke@example.test", "is_staff": True, "is_superuser": True},
    )
    if created:
        admin_user.set_password(PASSWORD)
        admin_user.save(update_fields=["password"])
    else:
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.set_password(PASSWORD)
        admin_user.save(update_fields=["is_staff", "is_superuser", "password"])

    category = Category.objects.create(
        name="Browser Smoke Category",
        slug="browser-smoke-category",
        is_active=True,
    )
    Product.objects.create(
        category=category,
        name="Browser Smoke Low Stock Product",
        slug="browser-smoke-low-stock-product",
        price=Decimal("125000"),
        stock=3,
        is_active=True,
    )
    order = Order.objects.create(
        user=admin_user,
        status=Order.Status.PAID,
        subtotal_amount=Decimal("125000"),
        discount_amount=Decimal("0"),
        shipping_amount=Decimal("0"),
        final_amount=Decimal("125000"),
    )
    payment = Payment.objects.create(
        order=order,
        amount=Decimal("125000"),
        status=Payment.Status.SUCCESS,
        gateway_name="mock_gateway",
    )


def wait_for_server(process: subprocess.Popen, log_path: Path) -> None:
    url = f"{BASE_URL}/admin/login/"
    for _ in range(45):
        if process.poll() is not None:
            raise RuntimeError(
                f"Development server exited with code {process.returncode}. "
                f"Log: {log_path.read_text(errors='replace')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError(
        f"Development server did not become ready. "
        f"Log: {log_path.read_text(errors='replace')}"
    )


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "base_url": BASE_URL,
        "viewport_checks": {},
        "important_static_assets": {},
        "console_errors": [],
        "page_errors": [],
        "screenshots": [],
    }
    prepare_database()

    env = os.environ.copy()
    log_path = ARTIFACTS / "browser-server.log"
    with log_path.open("w", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            [
                sys.executable,
                "manage.py",
                "runserver",
                "127.0.0.1:18765",
                "--noreload",
            ],
            cwd=ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_server(process, log_path)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1000},
                    device_scale_factor=1,
                )
                page.on(
                    "console",
                    lambda message: report["console_errors"].append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.on(
                    "pageerror",
                    lambda error: report["page_errors"].append(str(error)),
                )

                def capture_assets(response) -> None:
                    path = urlsplit(response.url).path
                    if path.endswith(
                        ("/static/css/admin_dashboard.css", "/static/js/admin_sidebar.js")
                    ):
                        report["important_static_assets"][path] = response.status

                page.on("response", capture_assets)

                response = page.goto(f"{BASE_URL}/admin/", wait_until="networkidle")
                if response is None or response.status not in (200, 302):
                    raise AssertionError(
                        f"Admin entry page returned {None if response is None else response.status}"
                    )

                if page.locator("#id_username").count():
                    page.locator("#id_username").fill(USERNAME)
                    page.locator("#id_password").fill(PASSWORD)
                    page.locator("input[type='submit']").click()
                page.locator(".store-admin-dashboard").wait_for(
                    state="visible", timeout=15000
                )

                assert page.locator("html").get_attribute("dir") == "rtl"
                assert page.locator("html").get_attribute("lang") == "fa-ir"
                assert page.get_by_text("جمع پرداخت‌های موفق", exact=True).is_visible()
                assert page.get_by_text("Browser Smoke Low Stock Product").count() == 0

                desktop_dimensions = page.evaluate(
                    "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                )
                report["viewport_checks"]["dashboard_desktop"] = desktop_dimensions
                if max(desktop_dimensions["document"], desktop_dimensions["body"]) > desktop_dimensions["viewport"]:
                    raise AssertionError(f"Desktop dashboard has horizontal overflow: {desktop_dimensions}")
                desktop_screenshot = ARTIFACTS / "admin-dashboard-desktop.png"
                page.screenshot(path=str(desktop_screenshot), full_page=True)
                report["screenshots"].append(str(desktop_screenshot.relative_to(ROOT)))

                search = page.locator("#nav-filter")
                search.fill("سفارش")
                order_group = page.locator(".store-nav-module").filter(has_text="فروش و سفارش‌ها").first
                product_group = page.locator(".store-nav-module").filter(has_text="محصولات و موجودی").first
                assert order_group.is_visible(), "Persian sidebar search hid the matching order group"
                assert not product_group.is_visible(), "Persian sidebar search did not hide unrelated product group"

                search.fill("zz-no-such-section")
                assert page.locator("#nav-filter-no-results").is_visible()
                search.fill("")
                assert page.locator("#nav-filter-no-results").is_hidden()

                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(150)
                mobile_dimensions = page.evaluate(
                    "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                )
                report["viewport_checks"]["dashboard_mobile"] = mobile_dimensions
                if max(mobile_dimensions["document"], mobile_dimensions["body"]) > mobile_dimensions["viewport"]:
                    raise AssertionError(f"Mobile dashboard has horizontal overflow: {mobile_dimensions}")
                mobile_screenshot = ARTIFACTS / "admin-dashboard-mobile.png"
                page.screenshot(path=str(mobile_screenshot), full_page=True)
                report["screenshots"].append(str(mobile_screenshot.relative_to(ROOT)))

                response = page.goto(
                    f"{BASE_URL}/admin/catalog/product/",
                    wait_until="networkidle",
                )
                if response is None or response.status != 200:
                    raise AssertionError(
                        f"Product changelist failed: {None if response is None else response.status}"
                    )
                product_list_diagnostics = page.evaluate(
                    """() => {
                        const viewport = window.innerWidth;
                        const round = value => Math.round(value * 100) / 100;
                        const overflowingElements = Array.from(document.querySelectorAll("body *"))
                            .map(element => {
                                const rect = element.getBoundingClientRect();
                                const style = window.getComputedStyle(element);
                                return {
                                    tag: element.tagName.toLowerCase(),
                                    id: element.id || null,
                                    className: typeof element.className === "string" ? element.className : "",
                                    text: (element.innerText || "").trim().replace(/\\s+/g, " ").slice(0, 100),
                                    left: round(rect.left),
                                    right: round(rect.right),
                                    width: round(rect.width),
                                    scrollWidth: element.scrollWidth,
                                    clientWidth: element.clientWidth,
                                    overflowX: style.overflowX,
                                    position: style.position,
                                };
                            })
                            .filter(element =>
                                element.width > 0 &&
                                (element.right > viewport + 2 || element.left < -2)
                            )
                            .sort((a, b) => b.right - a.right)
                            .slice(0, 40);
                        const layoutSelectors = [
                            "html",
                            "body",
                            "#main",
                            "#main > .content",
                            "#content",
                            "#content-main",
                            "#changelist",
                            "#changelist .changelist-form-container",
                            "#changelist-form",
                            "#changelist-form .actions",
                            "#changelist-form .results",
                            "#changelist-filter",
                            "#changelist .paginator",
                        ];
                        const layoutElements = layoutSelectors.map(selector => {
                            const element = document.querySelector(selector);
                            if (!element) {
                                return {selector, missing: true};
                            }
                            const rect = element.getBoundingClientRect();
                            const style = window.getComputedStyle(element);
                            return {
                                selector,
                                tag: element.tagName.toLowerCase(),
                                id: element.id || null,
                                className: typeof element.className === "string" ? element.className : "",
                                left: round(rect.left),
                                right: round(rect.right),
                                width: round(rect.width),
                                scrollWidth: element.scrollWidth,
                                clientWidth: element.clientWidth,
                                cssWidth: style.width,
                                maxWidth: style.maxWidth,
                                boxSizing: style.boxSizing,
                                overflowX: style.overflowX,
                                marginLeft: style.marginLeft,
                                marginRight: style.marginRight,
                                paddingLeft: style.paddingLeft,
                                paddingRight: style.paddingRight,
                                borderLeftWidth: style.borderLeftWidth,
                                borderRightWidth: style.borderRightWidth,
                            };
                        });
                        return {
                            viewport,
                            document: document.documentElement.scrollWidth,
                            documentClient: document.documentElement.clientWidth,
                            documentScrollLeft: document.documentElement.scrollLeft,
                            body: document.body.scrollWidth,
                            bodyClient: document.body.clientWidth,
                            windowScrollX: window.scrollX,
                            layoutElements,
                            overflowingElements,
                        };
                    }"""
                )
                report["viewport_checks"]["product_list_mobile"] = product_list_diagnostics
                product_list_screenshot = ARTIFACTS / "admin-products-mobile.png"
                page.screenshot(path=str(product_list_screenshot), full_page=True)
                report["screenshots"].append(str(product_list_screenshot.relative_to(ROOT)))
                if max(product_list_diagnostics["document"], product_list_diagnostics["body"]) > product_list_diagnostics["viewport"]:
                    raise AssertionError(
                        "Mobile product list has horizontal overflow; diagnostics: "
                        + json.dumps(product_list_diagnostics, ensure_ascii=False)
                    )

                response = page.goto(
                    f"{BASE_URL}/admin/catalog/product/add/",
                    wait_until="networkidle",
                )
                if response is None or response.status != 200:
                    raise AssertionError(
                        f"Product add form failed: {None if response is None else response.status}"
                    )
                product_form_dimensions = page.evaluate(
                    "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                )
                report["viewport_checks"]["product_form_mobile"] = product_form_dimensions
                if max(product_form_dimensions["document"], product_form_dimensions["body"]) > product_form_dimensions["viewport"]:
                    raise AssertionError(f"Mobile product form has horizontal overflow: {product_form_dimensions}")
                product_form_screenshot = ARTIFACTS / "admin-product-form-mobile.png"
                page.screenshot(path=str(product_form_screenshot), full_page=True)
                report["screenshots"].append(str(product_form_screenshot.relative_to(ROOT)))

                # Exercise the main seller operations pages in a narrow viewport.
                # These routes use the same persisted objects created for this smoke run.
                mobile_admin_pages = (
                    (
                        "orders_mobile",
                        f"{BASE_URL}/admin/orders/order/",
                        "change-list",
                        "admin-orders-mobile.png",
                    ),
                    (
                        "order_form_mobile",
                        f"{BASE_URL}/admin/orders/order/{order.pk}/change/",
                        "change-form",
                        "admin-order-form-mobile.png",
                    ),
                    (
                        "payments_mobile",
                        f"{BASE_URL}/admin/orders/payment/",
                        "change-list",
                        "admin-payments-mobile.png",
                    ),
                    (
                        "payment_form_mobile",
                        f"{BASE_URL}/admin/orders/payment/{payment.pk}/change/",
                        "change-form",
                        "admin-payment-form-mobile.png",
                    ),
                )
                for check_name, page_url, body_class, screenshot_name in mobile_admin_pages:
                    response = page.goto(page_url, wait_until="networkidle")
                    if response is None or response.status != 200:
                        raise AssertionError(
                            f"{check_name} returned {None if response is None else response.status}"
                        )
                    assert body_class in (page.locator("body").get_attribute("class") or "")
                    assert page.locator("html").get_attribute("dir") == "rtl"
                    assert page.locator("html").get_attribute("lang") == "fa-ir"
                    if body_class == "change-list":
                        assert page.locator("#changelist").count() == 1
                    else:
                        assert page.locator("#content-main > form").count() == 1

                    dimensions = page.evaluate(
                        "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                    )
                    report["viewport_checks"][check_name] = dimensions
                    if max(dimensions["document"], dimensions["body"]) > dimensions["viewport"]:
                        raise AssertionError(
                            f"{check_name} has horizontal overflow: {dimensions}"
                        )
                    screenshot_path = ARTIFACTS / screenshot_name
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    report["screenshots"].append(str(screenshot_path.relative_to(ROOT)))

                for asset_path in (
                    "/static/css/admin_dashboard.css",
                    "/static/js/admin_sidebar.js",
                ):
                    asset_status = report["important_static_assets"].get(asset_path)
                    assert asset_status in (200, 304), (
                        f"Admin asset did not load successfully (expected HTTP 200 or 304): "
                        f"{asset_path} status={asset_status} "
                        f"all_statuses={report['important_static_assets']}"
                    )

                if report["console_errors"]:
                    raise AssertionError(f"Browser console errors: {report['console_errors']}")
                if report["page_errors"]:
                    raise AssertionError(f"Browser JavaScript errors: {report['page_errors']}")

                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report_path = ARTIFACTS / "browser-smoke-report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
