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
})();
