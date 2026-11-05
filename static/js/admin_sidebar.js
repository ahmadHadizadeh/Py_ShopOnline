/*
 * Accessible, dependency-free search for the custom Django Admin sidebar.
 * Filters only navigation links already rendered for the current user's
 * permissions; it does not change authorization or create new destinations.
 */
(() => {
    "use strict";

    const input = document.getElementById("nav-filter");
    const menu = document.getElementById("store-sidebar-menu");
    const emptyState = document.getElementById("nav-filter-no-results");

    if (!input || !menu || !emptyState) {
        return;
    }

    const normalize = (value) => String(value || "")
        .normalize("NFKC")
        .toLocaleLowerCase("fa")
        .replace(/[ي]/g, "ی")
        .replace(/[ك]/g, "ک")
        .replace(/[\u064B-\u065F\u0670]/g, "")
        .trim();

    const groups = Array.from(menu.querySelectorAll(".store-nav-module"));

    const filterMenu = () => {
        const query = normalize(input.value);
        let hasVisibleItems = false;

        groups.forEach((group) => {
            const caption = group.querySelector("caption");
            const captionMatches = Boolean(
                query && caption && normalize(caption.textContent).includes(query)
            );
            const rows = Array.from(group.querySelectorAll("tbody tr"));
            let hasVisibleRows = false;

            rows.forEach((row) => {
                const matches = !query
                    || captionMatches
                    || normalize(row.textContent).includes(query);

                // Django's built-in nav_sidebar.js also filters this input
                // and writes inline display styles before this listener runs.
                // Synchronize both mechanisms so normalization/group matches win.
                row.hidden = !matches;
                row.style.display = matches ? "" : "none";
                hasVisibleRows = hasVisibleRows || matches;
            });

            group.hidden = Boolean(query && !captionMatches && !hasVisibleRows);
            hasVisibleItems = hasVisibleItems || hasVisibleRows;
        });

        emptyState.hidden = !query || hasVisibleItems;
        input.classList.toggle("no-results", Boolean(query && !hasVisibleItems));
    };

    // Register after Django's built-in listeners and re-apply normalized
    // visibility for every event type used by its quick filter.
    input.addEventListener("change", filterMenu);
    input.addEventListener("input", filterMenu);
    input.addEventListener("keyup", filterMenu);
    input.addEventListener("search", filterMenu);
    filterMenu();

    // Keep the Django-owned .shifted state and localStorage contract.
    // The custom layer only adds the responsive drawer affordances.
    const sidebar = document.getElementById("nav-sidebar");
    const main = document.getElementById("main");
    const toggle = document.getElementById("toggle-nav-sidebar");

    if (!sidebar || !main || !toggle) {
        return;
    }

    sidebar.querySelectorAll(".store-sidebar-link").forEach((link) => {
        const label = link.querySelector("span:not(.store-sidebar-external)")?.textContent.trim();
        if (label) {
            if (!link.hasAttribute("title")) {
                link.setAttribute("title", label);
            }
            if (!link.hasAttribute("aria-label")) {
                link.setAttribute("aria-label", label);
            }
        }
    });

    sidebar.querySelectorAll(".store-nav-module caption").forEach((caption) => {
        const label = caption.querySelector("span")?.textContent.trim();
        if (label) {
            if (!caption.hasAttribute("title")) {
                caption.setAttribute("title", label);
            }
            if (!caption.hasAttribute("aria-label")) {
                caption.setAttribute("aria-label", label);
            }
        }
    });


    const brandLink = sidebar.querySelector(".store-sidebar-brand-link");
    if (brandLink && !brandLink.hasAttribute("aria-label")) {
        brandLink.setAttribute("aria-label", "نمای کلی فروشگاه");
    }

    const footerLink = sidebar.querySelector(".store-sidebar-footer a");
    if (footerLink) {
        if (!footerLink.hasAttribute("title")) {
            footerLink.setAttribute("title", "مشاهده فروشگاه");
        }
        if (!footerLink.hasAttribute("aria-label")) {
            footerLink.setAttribute("aria-label", "مشاهده فروشگاه");
        }
    }

    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "store-sidebar-backdrop";
    backdrop.setAttribute("aria-label", "بستن منوی مدیریت");
    backdrop.setAttribute("title", "بستن منوی مدیریت");
    backdrop.hidden = true;
    main.insertBefore(backdrop, toggle.nextSibling);

    const drawerMedia = window.matchMedia("(max-width: 1024px)");

    const isDrawerOpen = () => drawerMedia.matches && main.classList.contains("shifted");

    toggle.setAttribute("aria-controls", "nav-sidebar");

    const syncDrawerState = () => {
        const expanded = main.classList.contains("shifted");
        const open = drawerMedia.matches && expanded;
        toggle.setAttribute("aria-expanded", String(expanded));
        backdrop.hidden = !open;
        document.body.classList.toggle("store-sidebar-drawer-open", open);
    };

    toggle.addEventListener("click", () => {
        syncDrawerState();
        if (isDrawerOpen()) {
            const firstLink = sidebar.querySelector(".store-sidebar-brand-link, .store-sidebar-link");
            firstLink?.focus({ preventScroll: true });
        } else if (drawerMedia.matches) {
            toggle.focus({ preventScroll: true });
        }
    });

    backdrop.addEventListener("click", () => {
        if (isDrawerOpen()) {
            toggle.click();
            toggle.focus({ preventScroll: true });
        }
    });

    document.addEventListener("keydown", (event) => {
        if (!isDrawerOpen()) {
            return;
        }

        if (event.key === "Escape") {
            event.preventDefault();
            toggle.click();
            toggle.focus({ preventScroll: true });
            return;
        }

        if (event.key !== "Tab") {
            return;
        }

        const focusable = Array.from(
            sidebar.querySelectorAll(
                'a[href]:not([aria-hidden="true"]), input:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )
        ).filter((element) => !element.hidden && element.getClientRects().length > 0);
        focusable.push(toggle);

        if (focusable.length < 2) {
            return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;

        if (event.shiftKey && (active === first || (!sidebar.contains(active) && active !== toggle))) {
            event.preventDefault();
            toggle.focus();
        } else if (!event.shiftKey && (active === last || active === toggle || !sidebar.contains(active))) {
            event.preventDefault();
            first.focus();
        }
    });

    let touchStart = null;

    document.addEventListener("pointerdown", (event) => {
        if (!drawerMedia.matches || event.pointerType === "mouse" || !event.isPrimary) {
            return;
        }

        const open = isDrawerOpen();
        const startedInsideSidebar = sidebar.contains(event.target);
        const startedAtRightEdge = event.clientX >= window.innerWidth - 24;

        if ((open && startedInsideSidebar) || (!open && startedAtRightEdge)) {
            touchStart = {
                x: event.clientX,
                y: event.clientY,
                open,
                pointerId: event.pointerId,
            };
        } else {
            touchStart = null;
        }
    }, { passive: true });

    document.addEventListener("pointerup", (event) => {
        if (!touchStart || event.pointerId !== touchStart.pointerId) {
            touchStart = null;
            return;
        }

        const deltaX = event.clientX - touchStart.x;
        const deltaY = event.clientY - touchStart.y;
        const shouldClose = touchStart.open && deltaX >= 64;
        const shouldOpen = !touchStart.open && deltaX <= -64;
        const horizontalGesture = Math.abs(deltaX) > Math.abs(deltaY) * 1.2;

        if (horizontalGesture && (shouldClose || shouldOpen)) {
            toggle.click();
        }
        touchStart = null;
    }, { passive: true });

    document.addEventListener("pointercancel", () => {
        touchStart = null;
    }, { passive: true });

    if (typeof drawerMedia.addEventListener === "function") {
        drawerMedia.addEventListener("change", syncDrawerState);
    } else {
        drawerMedia.addListener(syncDrawerState);
    }

    syncDrawerState();
})();
