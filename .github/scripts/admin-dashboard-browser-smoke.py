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
LIMITED_USERNAME = "browser-smoke-limited"
LIMITED_PASSWORD = "BrowserSmokeLimited_2026!"

os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"
os.environ["DJANGO_DEBUG"] = "1"
os.environ["DB_ENGINE"] = "sqlite3"
os.environ["PAYMENT_DEFAULT_GATEWAY"] = "mock_gateway"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import Permission  # noqa: E402
from django.core.management import call_command  # noqa: E402
from catalog.models.category import Category  # noqa: E402
from catalog.models.product import Product  # noqa: E402
from orders.models import Order, OrderAddressSnapshot, OrderItem, Payment  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402


def prepare_database() -> tuple[Order, Payment, Product, object]:
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

    limited_staff, _ = user_model.objects.get_or_create(
        username=LIMITED_USERNAME,
        defaults={"email": "browser-smoke-limited@example.test", "is_staff": True},
    )
    limited_staff.is_active = True
    limited_staff.is_staff = True
    limited_staff.is_superuser = False
    limited_staff.set_password(LIMITED_PASSWORD)
    limited_staff.save(update_fields=["is_active", "is_staff", "is_superuser", "password"])
    limited_staff.user_permissions.set(
        [
            Permission.objects.get(content_type__app_label="catalog", codename="view_product"),
            Permission.objects.get(content_type__app_label="orders", codename="view_payment"),
        ]
    )

    category = Category.objects.create(
        name="Browser Smoke Category",
        slug="browser-smoke-category",
        is_active=True,
    )
    product = Product.objects.create(
        category=category,
        name="Browser Smoke Low Stock Product",
        slug="browser-smoke-low-stock-product",
        price=Decimal("125000"),
        stock=3,
        is_active=True,
    )
    order = Order.objects.create(
        user=admin_user,
        status=Order.Status.PLACED,
        subtotal_amount=Decimal("125000"),
        discount_amount=Decimal("0"),
        shipping_amount=Decimal("0"),
        final_amount=Decimal("125000"),
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        variant_id=None,
        product_name=product.name,
        variant_name="",
        sku=None,
        quantity=1,
        unit_price=Decimal("125000"),
        subtotal_price=Decimal("125000"),
    )
    OrderAddressSnapshot.objects.create(
        order=order,
        recipient_name="گیرنده آزمایشی",
        recipient_mobile="09120000000",
        postal_code="1234567890",
        province="تهران",
        city="تهران",
        address_line="خیابان آزمایشی، پلاک ۱",
    )
    payment = Payment.objects.create(
        order=order,
        amount=Decimal("125000"),
        status=Payment.Status.PENDING,
        gateway_name="mock_gateway",
    )
    payment.update_status_and_order(
        Payment.Status.SUCCESS,
        transaction_id="BROWSER-SMOKE-TRANSACTION",
    )
    return order, payment, product, limited_staff


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
        "permission_checks": {},
        "important_static_assets": {},
        "console_errors": [],
        "page_errors": [],
        "screenshots": [],
    }
    order, payment, product, limited_staff = prepare_database()

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

                # Verify the new compact/expanded desktop geometry without losing menu entries.
                desktop_menu_snapshot = page.locator("#nav-sidebar .store-sidebar-link").evaluate_all(
                    "(links) => links.map(link => ({href: link.getAttribute('href'), label: link.textContent.trim()}))"
                )
                desktop_open_width = page.locator("#nav-sidebar").evaluate(
                    "(element) => element.getBoundingClientRect().width"
                )
                assert abs(desktop_open_width - 260) <= 1, (
                    f"Expanded desktop sidebar should be 260px: {desktop_open_width}"
                )

                sidebar_toggle = page.locator("#toggle-nav-sidebar")
                sidebar_toggle.click()
                page.wait_for_function(
                    "() => !document.getElementById('main').classList.contains('shifted') && Math.abs(document.getElementById('nav-sidebar').getBoundingClientRect().width - 64) <= 1",
                    timeout=2500,
                )
                compact_width = page.locator("#nav-sidebar").evaluate(
                    "(element) => element.getBoundingClientRect().width"
                )
                assert abs(compact_width - 64) <= 1, (
                    f"Compact desktop sidebar should be 64px: {compact_width}"
                )
                compact_menu_snapshot = page.locator("#nav-sidebar .store-sidebar-link").evaluate_all(
                    "(links) => links.map(link => ({href: link.getAttribute('href'), label: link.textContent.trim()}))"
                )
                assert compact_menu_snapshot == desktop_menu_snapshot, (
                    "Desktop compact mode changed or removed rendered navigation entries"
                )
                assert page.locator("#nav-filter").is_hidden(), (
                    "Compact desktop mode should hide the search field without removing it"
                )
                assert page.locator("#nav-sidebar .store-sidebar-link").first.get_attribute("aria-label"), (
                    "Compact navigation link is missing its accessible label"
                )
                assert page.locator("#nav-sidebar .store-sidebar-link").first.get_attribute("title"), (
                    "Compact navigation link is missing its tooltip"
                )
                assert page.locator("#nav-sidebar .store-nav-module caption").first.get_attribute("title"), (
                    "Compact navigation group is missing its tooltip"
                )
                compact_sidebar_diagnostics = page.locator("#nav-sidebar").evaluate(
                    """sidebar => {
                        const bounds = sidebar.getBoundingClientRect();
                        const nodes = Array.from(sidebar.querySelectorAll(
                            ".store-sidebar-brand-mark, .store-sidebar-link svg, .store-nav-module caption svg"
                        )).map(element => {
                            const rect = element.getBoundingClientRect();
                            const style = getComputedStyle(element);
                            return {
                                selector: element.className?.baseVal || element.className || element.tagName.toLowerCase(),
                                left: Math.round(rect.left * 100) / 100,
                                right: Math.round(rect.right * 100) / 100,
                                width: Math.round(rect.width * 100) / 100,
                                display: style.display,
                                visibility: style.visibility,
                                insideSidebar: rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1,
                            };
                        });
                        return {
                            width: bounds.width,
                            paddingLeft: getComputedStyle(sidebar).paddingLeft,
                            paddingRight: getComputedStyle(sidebar).paddingRight,
                            scrollWidth: sidebar.scrollWidth,
                            clientWidth: sidebar.clientWidth,
                            nodes,
                        };
                    }"""
                )
                clipped_icons = [
                    node for node in compact_sidebar_diagnostics["nodes"]
                    if not node["insideSidebar"] or node["width"] <= 0 or node["visibility"] != "visible"
                ]
                assert not clipped_icons, (
                    "Compact rail clips its brand or navigation icons: "
                    f"{compact_sidebar_diagnostics}"
                )
                report["viewport_checks"]["desktop_compact_sidebar"] = compact_sidebar_diagnostics
                compact_screenshot = ARTIFACTS / "admin-dashboard-desktop-compact.png"
                page.screenshot(path=str(compact_screenshot), full_page=True)
                report["screenshots"].append(str(compact_screenshot.relative_to(ROOT)))
                sidebar_toggle.click()
                page.wait_for_function(
                    "() => document.getElementById('main').classList.contains('shifted') && Math.abs(document.getElementById('nav-sidebar').getBoundingClientRect().width - 260) <= 1",
                    timeout=2500,
                )
                reopened_width = page.locator("#nav-sidebar").evaluate(
                    "(element) => element.getBoundingClientRect().width"
                )
                assert abs(reopened_width - 260) <= 1, (
                    f"Desktop sidebar did not reopen at 260px: {reopened_width}"
                )
                assert page.locator("#nav-filter").is_visible()

                search = page.locator("#nav-filter")
                order_group = page.locator(".store-nav-module").filter(has_text="فروش و سفارش‌ها").first
                product_group = page.locator(".store-nav-module").filter(has_text="محصولات و موجودی").first
                customer_group = page.locator(".store-nav-module").filter(has_text="مشتریان و کاربران").first

                # Searching a group caption should reveal every link in that group.
                search.fill("فروش و سفارش‌ها")
                assert order_group.is_visible(), "Group-caption search hid the matching order group"
                assert order_group.locator("a").filter(has_text="پرداخت‌ها و تراکنش‌ها").is_visible(), (
                    "Group-caption search did not reveal all links in the matching group"
                )
                assert not product_group.is_visible(), "Group-caption search did not hide unrelated product group"

                # Arabic Yeh must normalize to Persian Yeh despite Django's native quick filter.
                search.fill("مشتري")
                assert customer_group.is_visible(), "Arabic Yeh was not normalized in Persian sidebar search"
                assert customer_group.locator("a").filter(has_text="پروفایل و تأیید مشتریان").is_visible(), (
                    "Normalized customer search did not reveal the matching link"
                )

                # Retain a normal Persian word-search regression as well.
                search.fill("سفارش")
                assert order_group.is_visible(), "Persian sidebar search hid the matching order group"
                assert not product_group.is_visible(), "Persian sidebar search did not hide unrelated product group"

                search.fill("zz-no-such-section")
                assert page.locator("#nav-filter-no-results").is_visible()
                search.fill("")
                assert page.locator("#nav-filter-no-results").is_hidden()

                # Tablet (portrait-class viewport): first visit starts closed, then uses a drawer.
                page.set_viewport_size({"width": 820, "height": 1180})
                page.evaluate(
                    "() => localStorage.removeItem('django.admin.navSidebarIsOpen')"
                )
                page.reload(wait_until="networkidle")
                tablet_sidebar = page.locator("#nav-sidebar")
                tablet_toggle = page.locator("#toggle-nav-sidebar")
                tablet_sidebar.wait_for(state="hidden", timeout=1500)
                assert tablet_toggle.is_visible(), "Tablet drawer toggle is not visible"
                tablet_toggle.click()
                tablet_sidebar.wait_for(state="visible", timeout=1500)
                tablet_backdrop = page.locator(".store-sidebar-backdrop")
                assert tablet_backdrop.is_visible(), "Tablet drawer backdrop is missing"
                tablet_width = tablet_sidebar.evaluate(
                    "(element) => element.getBoundingClientRect().width"
                )
                assert tablet_width <= 320 and tablet_width > 0, (
                    f"Tablet drawer width is outside the intended range: {tablet_width}"
                )
                tablet_screenshot = ARTIFACTS / "admin-dashboard-tablet-drawer.png"
                page.screenshot(path=str(tablet_screenshot), full_page=True)
                report["screenshots"].append(str(tablet_screenshot.relative_to(ROOT)))
                page.keyboard.press("Escape")
                tablet_sidebar.wait_for(state="hidden", timeout=1500)
                assert tablet_backdrop.is_hidden(), "Escape did not close the tablet drawer"

                tablet_toggle.click()
                assert tablet_backdrop.is_visible()
                tablet_backdrop.click(position={"x": 20, "y": 20})
                tablet_sidebar.wait_for(state="hidden", timeout=1500)

                # Verify right-edge swipe-to-open and in-drawer swipe-to-close.
                page.dispatch_event(
                    "body",
                    "pointerdown",
                    {"pointerType": "touch", "isPrimary": True, "pointerId": 41,
                     "clientX": 810, "clientY": 280},
                )
                page.dispatch_event(
                    "body",
                    "pointerup",
                    {"pointerType": "touch", "isPrimary": True, "pointerId": 41,
                     "clientX": 720, "clientY": 284},
                )
                tablet_sidebar.wait_for(state="visible", timeout=1500)
                page.dispatch_event(
                    "#nav-sidebar",
                    "pointerdown",
                    {"pointerType": "touch", "isPrimary": True, "pointerId": 42,
                     "clientX": 700, "clientY": 300},
                )
                page.dispatch_event(
                    "#nav-sidebar",
                    "pointerup",
                    {"pointerType": "touch", "isPrimary": True, "pointerId": 42,
                     "clientX": 780, "clientY": 304},
                )
                tablet_sidebar.wait_for(state="hidden", timeout=1500)
                tablet_dimensions = page.evaluate(
                    "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                )
                report["viewport_checks"]["dashboard_tablet"] = tablet_dimensions
                if max(tablet_dimensions["document"], tablet_dimensions["body"]) > tablet_dimensions["viewport"]:
                    raise AssertionError(f"Tablet dashboard has horizontal overflow: {tablet_dimensions}")

                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate(
                    "() => localStorage.removeItem('django.admin.navSidebarIsOpen')"
                )
                page.reload(wait_until="networkidle")
                mobile_sidebar = page.locator("#nav-sidebar")
                sidebar_toggle = page.locator("#toggle-nav-sidebar")
                mobile_sidebar.wait_for(state="hidden", timeout=1500)
                sidebar_toggle.click()
                mobile_sidebar.wait_for(state="visible", timeout=1500)
                mobile_backdrop = page.locator(".store-sidebar-backdrop")
                assert mobile_backdrop.is_visible(), "Mobile drawer backdrop is missing"
                mobile_drawer_screenshot = ARTIFACTS / "admin-dashboard-mobile-drawer.png"
                page.screenshot(path=str(mobile_drawer_screenshot), full_page=True)
                report["screenshots"].append(str(mobile_drawer_screenshot.relative_to(ROOT)))
                mobile_backdrop.click(position={"x": 15, "y": 15})
                mobile_sidebar.wait_for(state="hidden", timeout=1500)
                sidebar_toggle.click()
                mobile_sidebar.wait_for(state="visible", timeout=1500)
                page.keyboard.press("Escape")
                mobile_sidebar.wait_for(state="hidden", timeout=1500)
                mobile_dimensions = page.evaluate(
                    "() => ({viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth})"
                )
                report["viewport_checks"]["dashboard_mobile"] = mobile_dimensions
                if max(mobile_dimensions["document"], mobile_dimensions["body"]) > mobile_dimensions["viewport"]:
                    raise AssertionError(f"Mobile dashboard has horizontal overflow: {mobile_dimensions}")
                mobile_screenshot = ARTIFACTS / "admin-dashboard-mobile.png"
                page.screenshot(path=str(mobile_screenshot), full_page=True)
                report["screenshots"].append(str(mobile_screenshot.relative_to(ROOT)))

                # Explicit light-theme pass: shell should remain readable and usable.
                page.evaluate("() => localStorage.setItem('theme', 'light')")
                page.emulate_media(color_scheme="light")
                response = page.goto(
                    f"{BASE_URL}/admin/catalog/product/",
                    wait_until="networkidle",
                )
                if response is None or response.status != 200:
                    raise AssertionError(
                        f"Light-theme product changelist failed: {None if response is None else response.status}"
                    )
                assert page.locator("html").get_attribute("data-theme") == "light"
                sidebar_toggle = page.locator("#toggle-nav-sidebar")
                sidebar_toggle.click()
                assert page.locator("#nav-sidebar").is_visible()
                light_sidebar_style = page.locator("#nav-sidebar").evaluate(
                    "(element) => ({color: getComputedStyle(element).color, backgroundImage: getComputedStyle(element).backgroundImage})"
                )
                assert light_sidebar_style["backgroundImage"] != "none", (
                    f"Light-theme sidebar background did not render: {light_sidebar_style}"
                )
                sidebar_toggle.click()
                page.wait_for_function(
                    "() => !document.getElementById('main').classList.contains('shifted') && document.querySelector('.store-sidebar-backdrop').hidden && !document.body.classList.contains('store-sidebar-drawer-open')",
                    timeout=2000,
                )

                # Exercise the explicitly selected dark theme.
                page.evaluate("() => localStorage.setItem('theme', 'dark')")
                response = page.goto(
                    f"{BASE_URL}/admin/catalog/product/",
                    wait_until="networkidle",
                )
                if response is None or response.status != 200:
                    raise AssertionError(
                        f"Product changelist failed: {None if response is None else response.status}"
                    )
                assert page.locator("html").get_attribute("data-theme") == "dark"
                sidebar_toggle = page.locator("#toggle-nav-sidebar")
                sidebar_toggle.click()
                dark_sidebar_style = page.locator("#nav-sidebar").evaluate(
                    "(element) => ({color: getComputedStyle(element).color, backgroundImage: getComputedStyle(element).backgroundImage})"
                )
                assert dark_sidebar_style["backgroundImage"] != "none", (
                    f"Dark-theme sidebar background did not render: {dark_sidebar_style}"
                )
                assert dark_sidebar_style["color"] == light_sidebar_style["color"], (
                    f"Sidebar foreground changed unexpectedly between themes: light={light_sidebar_style}, dark={dark_sidebar_style}"
                )
                sidebar_toggle.click()
                page.wait_for_function(
                    "() => !document.getElementById('main').classList.contains('shifted') && document.querySelector('.store-sidebar-backdrop').hidden && !document.body.classList.contains('store-sidebar-drawer-open')",
                    timeout=2000,
                )
                dark_theme_contrast = page.evaluate(
                    """() => {
                        const row = document.querySelector("#changelist table tbody tr");
                        if (!row) {
                            return {error: "No product data row available for contrast check"};
                        }
                        const cell = Array.from(row.querySelectorAll("th, td"))
                            .find(element => !element.classList.contains("action-checkbox"));
                        if (!cell) {
                            return {error: "No readable product cell available for contrast check"};
                        }
                        const textElement = cell.querySelector("a") || cell;
                        const rgb = value => (value.match(/[0-9.]+/g) || []).slice(0, 3).map(Number);
                        const linear = channel => {
                            channel /= 255;
                            return channel <= 0.04045
                                ? channel / 12.92
                                : Math.pow((channel + 0.055) / 1.055, 2.4);
                        };
                        const luminance = value => {
                            const channels = rgb(value);
                            if (channels.length !== 3) return null;
                            const [red, green, blue] = channels.map(linear);
                            return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
                        };
                        const background = getComputedStyle(row).backgroundColor;
                        const foreground = getComputedStyle(textElement).color;
                        const light = luminance(background);
                        const dark = luminance(foreground);
                        if (light === null || dark === null) {
                            return {error: "Could not resolve computed foreground/background colors"};
                        }
                        return {
                            theme: document.documentElement.dataset.theme,
                            background,
                            foreground,
                            ratio: (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05),
                        };
                    }"""
                )
                assert dark_theme_contrast.get("theme") == "dark", dark_theme_contrast
                assert dark_theme_contrast.get("ratio", 0) >= 4.5, (
                    f"Dark-theme table text contrast is below WCAG AA: {dark_theme_contrast}"
                )

                # Also cover automatic dark mode when the operating system prefers dark.
                page.emulate_media(color_scheme="dark")
                page.evaluate(
                    """() => {
                        localStorage.setItem("theme", "auto");
                        document.documentElement.dataset.theme = "auto";
                    }"""
                )
                auto_dark_row_background = page.locator(
                    "#changelist table tbody tr"
                ).first.evaluate("(row) => getComputedStyle(row).backgroundColor")
                assert auto_dark_row_background == "rgb(255, 255, 255)", (
                    "System-auto dark mode did not retain readable light table rows: "
                    f"{auto_dark_row_background}"
                )
                assert not page.locator("#main").evaluate("(element) => element.classList.contains('shifted')"), (
                    "Product changelist screenshot would be obscured by an open navigation drawer"
                )
                assert page.locator(".store-sidebar-backdrop").is_hidden(), (
                    "Product changelist screenshot would be obscured by the drawer backdrop"
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
                        "product_change_mobile",
                        f"{BASE_URL}/admin/catalog/product/{product.pk}/change/",
                        "change-form",
                        "admin-product-change-mobile.png",
                    ),
                    (
                        "inventory_operations_mobile",
                        f"{BASE_URL}/admin/catalog/product/{product.pk}/inventory/",
                        "inventory-operation",
                        "admin-inventory-operations-mobile.png",
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
                    elif body_class == "change-form":
                        assert page.locator("#content-main > form").count() == 1
                    else:
                        assert page.locator("#content-main form").count() == 1
                        assert page.locator(
                            "#content-main .store-inventory-history table#result_list"
                        ).count() == 1
                        if body_class == "inventory-operation":
                            assert page.locator("#content h1").count() == 1, (
                                "Inventory page renders a duplicated page heading"
                            )
                        assert page.get_by_role(
                            "heading", name="آخرین گردش‌های موجودی"
                        ).count() == 1

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

                # Verify rendered navigation still follows Django object permissions.
                limited_page = browser.new_page(
                    viewport={"width": 1440, "height": 1000},
                    device_scale_factor=1,
                )
                limited_response = limited_page.goto(
                    f"{BASE_URL}/admin/",
                    wait_until="networkidle",
                )
                if limited_response is None or limited_response.status not in (200, 302):
                    raise AssertionError(
                        f"Limited-staff admin entry failed: {None if limited_response is None else limited_response.status}"
                    )
                if limited_page.locator("#id_username").count():
                    limited_page.locator("#id_username").fill(LIMITED_USERNAME)
                    limited_page.locator("#id_password").fill(LIMITED_PASSWORD)
                    limited_page.locator("input[type='submit']").click()
                limited_page.locator("#nav-sidebar").wait_for(state="visible", timeout=15000)
                allowed_links = limited_page.locator(
                    "#nav-sidebar .store-sidebar-link"
                ).evaluate_all(
                    "(links) => links.map(link => ({href: link.getAttribute('href'), label: link.textContent.trim()}))"
                )
                allowed_hrefs = {item["href"] for item in allowed_links}
                assert "/admin/catalog/product/" in allowed_hrefs, (
                    f"Limited staff lost its permitted product navigation: {allowed_links}"
                )
                assert "/admin/orders/payment/" in allowed_hrefs, (
                    f"Limited staff lost its permitted payment navigation: {allowed_links}"
                )
                assert "/admin/orders/order/" not in allowed_hrefs, (
                    f"Limited staff saw an order link without order permission: {allowed_links}"
                )
                assert "/admin/catalog/category/" not in allowed_hrefs, (
                    f"Limited staff saw a category link without category permission: {allowed_links}"
                )
                assert "/admin/auth/user/" not in allowed_hrefs, (
                    f"Limited staff saw user administration without auth permission: {allowed_links}"
                )
                report["permission_checks"]["limited_staff_links"] = allowed_links
                limited_page.close()

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
