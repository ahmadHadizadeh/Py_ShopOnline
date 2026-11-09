(() => {
    "use strict";

    const inputs = document.querySelectorAll("[data-search-suggest-url]");

    inputs.forEach((input) => {
        const form = input.closest("form");
        const panel = form?.querySelector("[data-search-suggestions]");
        if (!form || !panel) return;

        let debounceTimer = null;
        let requestController = null;

        const setExpanded = (expanded) => {
            input.setAttribute("aria-expanded", String(expanded));
            panel.setAttribute("aria-hidden", String(!expanded));
            panel.classList.toggle("hidden", !expanded);
        };

        const hideSuggestions = () => {
            panel.replaceChildren();
            setExpanded(false);
        };

        const makeSuggestion = (item) => {
            const link = document.createElement("a");
            link.href = item.url;
            link.className = "flex items-center justify-between gap-3 px-4 py-3 text-sm text-gray-700 hover:bg-indigo-50";
            link.setAttribute("role", "option");

            const details = document.createElement("span");
            details.className = "flex min-w-0 flex-col";

            const name = document.createElement("span");
            name.className = "truncate font-bold";
            name.textContent = item.name;

            const category = document.createElement("span");
            category.className = "mt-1 truncate text-xs text-gray-500";
            category.textContent = item.category || "";

            details.append(name, category);

            const price = document.createElement("span");
            price.className = "shrink-0 text-xs font-bold text-indigo-600";
            const numericPrice = Number(item.price);
            price.textContent = Number.isFinite(numericPrice)
                ? `${new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 0 }).format(numericPrice)} تومان`
                : "";

            link.append(details, price);
            return link;
        };

        const loadSuggestions = async (query) => {
            requestController?.abort();
            requestController = new AbortController();

            const endpoint = new URL(input.dataset.searchSuggestUrl, window.location.origin);
            endpoint.searchParams.set("q", query);

            const categorySlug = input.dataset.categorySlug || "";
            if (categorySlug) endpoint.searchParams.set("category", categorySlug);

            try {
                const response = await fetch(endpoint.toString(), {
                    method: "GET",
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                    signal: requestController.signal,
                });
                if (!response.ok) throw new Error(`Search suggestions returned HTTP ${response.status}`);

                const payload = await response.json();
                if (input.value.trim().slice(0, 120) !== query) return;

                const results = Array.isArray(payload.results) ? payload.results.slice(0, 6) : [];
                panel.replaceChildren();

                results.forEach((item) => {
                    if (!item || !item.name || !item.url) return;
                    panel.appendChild(makeSuggestion(item));
                });

                setExpanded(panel.childElementCount > 0);
            } catch (error) {
                if (error.name !== "AbortError") hideSuggestions();
            }
        };

        input.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            requestController?.abort();

            const query = input.value.trim().slice(0, 120);
            if (query.length < 2) {
                hideSuggestions();
                return;
            }

            debounceTimer = window.setTimeout(() => loadSuggestions(query), 180);
        });

        input.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                if (!panel.classList.contains("hidden")) {
                    event.preventDefault();
                    hideSuggestions();
                }
                return;
            }

            if (event.key === "ArrowDown" && !panel.classList.contains("hidden")) {
                const firstOption = panel.querySelector("a[role='option']");
                if (firstOption) {
                    event.preventDefault();
                    firstOption.focus();
                }
            }
        });

        panel.addEventListener("keydown", (event) => {
            const options = Array.from(panel.querySelectorAll("a[role='option']"));
            const currentIndex = options.indexOf(document.activeElement);

            if (event.key === "Escape") {
                event.preventDefault();
                hideSuggestions();
                input.focus();
            } else if (event.key === "ArrowDown" && currentIndex < options.length - 1) {
                event.preventDefault();
                options[currentIndex + 1].focus();
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                if (currentIndex <= 0) input.focus();
                else options[currentIndex - 1].focus();
            }
        });

        document.addEventListener("click", (event) => {
            if (!form.contains(event.target)) hideSuggestions();
        });

        form.addEventListener("submit", hideSuggestions);
    });
})();
