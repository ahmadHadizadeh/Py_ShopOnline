from __future__ import annotations

import json
import os
import re
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
    Payment.objects.create(
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
                    path = response.url.split("?", 1)[0]
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
                if desktop_dimensions["document"] > desktop_dimensions["viewport"] + 2:
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
                if mobile_dimensions["document"] > mobile_dimensions["viewport"] + 2:
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
                product_list_dimensions = page.evaluate(
                    "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                )
                report["viewport_checks"]["product_list_mobile"] = product_list_dimensions
                if product_list_dimensions["document"] > product_list_dimensions["viewport"] + 2:
                    raise AssertionError(f"Mobile product list has horizontal overflow: {product_list_dimensions}")
                product_list_screenshot = ARTIFACTS / "admin-products-mobile.png"
                page.screenshot(path=str(product_list_screenshot), full_page=True)
                report["screenshots"].append(str(product_list_screenshot.relative_to(ROOT)))

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
                if product_form_dimensions["document"] > product_form_dimensions["viewport"] + 2:
                    raise AssertionError(f"Mobile product form has horizontal overflow: {product_form_dimensions}")
                product_form_screenshot = ARTIFACTS / "admin-product-form-mobile.png"
                page.screenshot(path=str(product_form_screenshot), full_page=True)
                report["screenshots"].append(str(product_form_screenshot.relative_to(ROOT)))

                for asset_path in (
                    "/static/css/admin_dashboard.css",
                    "/static/js/admin_sidebar.js",
                ):
                    assert report["important_static_assets"].get(asset_path) == 200, (
                        f"Admin asset did not load with HTTP 200: {asset_path} "
                        f"statuses={report['important_static_assets']}"
                    )

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
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
