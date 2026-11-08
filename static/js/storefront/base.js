(() => {
    "use strict";

    const loader = document.getElementById("global-loader");
    let hideTimer = null;

    if (loader) {
        window.hideLoader = (immediate = false) => {
            clearTimeout(hideTimer);
            if (immediate) {
                loader.classList.add("hidden", "opacity-0", "pointer-events-none");
                loader.classList.remove("flex", "opacity-100");
                return;
            }
            loader.classList.remove("opacity-100");
            hideTimer = setTimeout(() => {
                loader.classList.add("hidden");
                loader.classList.remove("flex");
            }, 200);
        };

        window.showLoader = () => {
            clearTimeout(hideTimer);
            loader.classList.remove("hidden", "opacity-0", "pointer-events-none");
            requestAnimationFrame(() => loader.classList.add("flex", "opacity-100"));
        };

        window.addEventListener("pageshow", () => window.hideLoader(true));
        window.addEventListener("load", () => window.hideLoader(true));

        document.addEventListener("click", (event) => {
            const target = event.target instanceof Element ? event.target : null;
            const link = target ? target.closest("a") : null;
            if (!link || link.target === "_blank" || link.getAttribute("href")?.startsWith("#")) return;
            window.showLoader();
        });

        document.addEventListener("submit", () => window.showLoader());
    }

    const canHover = typeof window.matchMedia === "function"
        && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

    function bindDisclosure(wrapper, trigger, menu, chevron) {
        if (!wrapper || !trigger || !menu) return;
        let closeTimer = null;
        const isOpen = () => trigger.getAttribute("aria-expanded") === "true";

        const setOpen = (open) => {
            clearTimeout(closeTimer);
            trigger.setAttribute("aria-expanded", String(open));
            menu.setAttribute("aria-hidden", String(!open));
            if (open) {
                menu.classList.remove("invisible", "opacity-0", "translate-y-2", "pointer-events-none");
                menu.classList.add("visible", "opacity-100", "translate-y-0", "pointer-events-auto");
                if (chevron) chevron.classList.add("rotate-180");
            } else {
                menu.classList.add("invisible", "opacity-0", "translate-y-2", "pointer-events-none");
                menu.classList.remove("visible", "opacity-100", "translate-y-0", "pointer-events-auto");
                if (chevron) chevron.classList.remove("rotate-180");
            }
        };

        const scheduleClose = () => {
            clearTimeout(closeTimer);
            closeTimer = setTimeout(() => {
                if (wrapper.contains(document.activeElement)) return;
                setOpen(false);
            }, 200);
        };

        trigger.addEventListener("click", (event) => {
            if (canHover && event.detail > 0) {
                setOpen(true);
                return;
            }
            setOpen(!isOpen());
        });

        if (canHover) {
            wrapper.addEventListener("pointerenter", () => setOpen(true));
            wrapper.addEventListener("pointerleave", scheduleClose);
        }

        wrapper.addEventListener("focusin", () => clearTimeout(closeTimer));
        wrapper.addEventListener("focusout", (event) => {
            if (!wrapper.contains(event.relatedTarget)) scheduleClose();
        });

        document.addEventListener("click", (event) => {
            if (!wrapper.contains(event.target)) setOpen(false);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && isOpen()) {
                setOpen(false);
                trigger.focus();
            }
        });
    }

    bindDisclosure(
        document.getElementById("account-dropdown-wrapper"),
        document.getElementById("accountDropdown"),
        document.getElementById("accountMenu"),
        document.getElementById("accountChevron")
    );
    bindDisclosure(
        document.getElementById("desktop-category-dropdown"),
        document.getElementById("desktop-categories-toggle"),
        document.getElementById("desktop-categories-menu"),
        document.getElementById("desktop-categories-chevron")
    );

    const header = document.getElementById("storefront-header");
    const navButton = document.getElementById("mobile-nav-toggle");
    const navPanel = document.getElementById("mobile-navigation-panel");
    const navIcon = document.getElementById("mobile-nav-icon");
    const searchButton = document.getElementById("mobile-search-toggle");
    const searchPanel = document.getElementById("mobile-search-panel");
    const searchIcon = document.getElementById("mobile-search-icon");

    function setPanel(button, panel, icon, open, defaultIcon) {
        if (!button || !panel) return;
        panel.classList.toggle("hidden", !open);
        panel.setAttribute("aria-hidden", String(!open));
        button.setAttribute("aria-expanded", String(open));
        if (icon) {
            icon.classList.toggle(defaultIcon, !open);
            icon.classList.toggle("fa-xmark", open);
        }
    }

    function closeMobilePanels() {
        setPanel(navButton, navPanel, navIcon, false, "fa-bars");
        setPanel(searchButton, searchPanel, searchIcon, false, "fa-magnifying-glass");
    }

    if (navButton && navPanel) {
        navButton.addEventListener("click", () => {
            const open = navPanel.classList.contains("hidden");
            setPanel(navButton, navPanel, navIcon, open, "fa-bars");
            if (open) setPanel(searchButton, searchPanel, searchIcon, false, "fa-magnifying-glass");
        });
    }

    if (searchButton && searchPanel) {
        searchButton.addEventListener("click", () => {
            const open = searchPanel.classList.contains("hidden");
            setPanel(searchButton, searchPanel, searchIcon, open, "fa-magnifying-glass");
            if (open) {
                setPanel(navButton, navPanel, navIcon, false, "fa-bars");
                const searchInput = searchPanel.querySelector('input[name="q"]');
                if (searchInput) searchInput.focus();
            }
        });
    }

    document.addEventListener("click", (event) => {
        if (header && !header.contains(event.target)) closeMobilePanels();
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const navWasOpen = navPanel && !navPanel.classList.contains("hidden");
        const searchWasOpen = searchPanel && !searchPanel.classList.contains("hidden");
        if (!navWasOpen && !searchWasOpen) return;
        closeMobilePanels();
        if (navWasOpen && navButton) navButton.focus();
        else if (searchButton) searchButton.focus();
    });

    if (typeof window.matchMedia === "function") {
        const desktopMedia = window.matchMedia("(min-width: 768px)");
        const searchMedia = window.matchMedia("(min-width: 640px)");
        const onBreakpointChange = (event) => {
            if (event.matches) closeMobilePanels();
        };
        if (typeof desktopMedia.addEventListener === "function") {
            desktopMedia.addEventListener("change", onBreakpointChange);
            searchMedia.addEventListener("change", onBreakpointChange);
        } else if (typeof desktopMedia.addListener === "function") {
            desktopMedia.addListener(onBreakpointChange);
            searchMedia.addListener(onBreakpointChange);
        }
    }
})();
