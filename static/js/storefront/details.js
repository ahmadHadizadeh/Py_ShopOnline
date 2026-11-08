(() => {
    "use strict";

    document.addEventListener("DOMContentLoaded", () => {
        const mainImage = document.getElementById("main-product-image");
        const zoomStage = document.getElementById("image-zoom-stage");
        const thumbnailButtons = document.querySelectorAll(".thumbnail-button");
        const imageModal = document.getElementById("image-modal");
        const modalImage = document.getElementById("modal-product-image");
        const openModalButton = document.getElementById("open-image-modal");
        const closeModalButton = document.getElementById("close-image-modal");
        const zoomInButton = document.getElementById("zoom-in");
        const zoomOutButton = document.getElementById("zoom-out");
        const zoomResetButton = document.getElementById("zoom-reset");
        const ratingInput = document.getElementById("rating-input");
        const ratingButtons = document.querySelectorAll(".rating-button");
        const ratingLabel = document.getElementById("rating-label");
        const wishlistButton = document.getElementById("wishlist-btn");
        const wishlistMessage = document.getElementById("wishlist-message");
        const variantSelect = document.getElementById("product-variant");
        const priceDisplay = document.getElementById("product-price-display");
        const oldPriceDisplay = document.getElementById("product-old-price-display");
        const discountBadge = document.getElementById("product-discount-badge");

        if (variantSelect && priceDisplay) {
            const basePrice = Number(priceDisplay.dataset.basePrice || 0);
            const oldPrice = oldPriceDisplay
                ? Number(oldPriceDisplay.dataset.oldPrice)
                : Number.NaN;
            const formatPrice = value => new Intl.NumberFormat("fa-IR", {
                maximumFractionDigits: 0,
            }).format(value);
            variantSelect.addEventListener("change", () => {
                const selected = variantSelect.options[variantSelect.selectedIndex];
                const adjustment = Number(selected?.dataset.priceAdjustment || 0);
                const finalPrice = basePrice + adjustment;
                const hasDiscount = Number.isFinite(finalPrice)
                    && Number.isFinite(oldPrice)
                    && oldPrice > finalPrice;

                priceDisplay.textContent = formatPrice(finalPrice);
                if (oldPriceDisplay) {
                    oldPriceDisplay.classList.toggle("hidden", !hasDiscount);
                }
                if (discountBadge) {
                    discountBadge.classList.toggle("hidden", !hasDiscount);
                }
            });
        }

        document.querySelectorAll("[data-quantity-action]").forEach(button => {
            button.addEventListener("click", () => {
                const input = button.closest("form")?.querySelector('input[name="quantity"]');
                if (!input) return;
                const current = Number(input.value);
                const min = input.min === "" ? 1 : Number(input.min);
                const max = input.max === "" ? Number.POSITIVE_INFINITY : Number(input.max);
                if (button.dataset.quantityAction === "decrease" && current > min) {
                    input.stepDown();
                } else if (button.dataset.quantityAction === "increase" && current < max) {
                    input.stepUp();
                }
            });
        });

        function setRating(value) {
            if (!ratingInput) return;
            ratingInput.value = value;
            ratingButtons.forEach(button => {
                const icon = button.querySelector("i");
                if (!icon) return;
                const selected = Number(button.dataset.rating) <= value;
                icon.classList.toggle("fa-solid", selected);
                icon.classList.toggle("text-yellow-400", selected);
                icon.classList.toggle("fa-regular", !selected);
                icon.classList.toggle("text-gray-300", !selected);
            });
            if (ratingLabel) ratingLabel.textContent = `امتیاز انتخابی: ${value} از 5`;
        }
        ratingButtons.forEach(button => button.addEventListener("click", () => {
            setRating(Number(button.dataset.rating));
        }));
        if (ratingButtons.length) setRating(5);

        const reviewMessage = document.getElementById("review-message");
        const reviewForm = document.getElementById("review-form");
        const reviewsContainer = document.getElementById("reviews-container");
        const reviewCountBadge = document.getElementById("review-count-badge");

        function showReviewMessage(message, isError) {
            if (!reviewMessage) return;
            reviewMessage.textContent = message;
            reviewMessage.classList.remove(
                "hidden", "bg-emerald-50", "border-emerald-200", "text-emerald-700",
                "bg-rose-50", "border-rose-200", "text-rose-700"
            );
            if (isError) {
                reviewMessage.classList.add("bg-rose-50", "border-rose-200", "text-rose-700");
            } else {
                reviewMessage.classList.add("bg-emerald-50", "border-emerald-200", "text-emerald-700");
            }
        }

        // The review form is only rendered for authenticated users.
        // Its absence must not stop gallery, quantity, zoom, or wishlist handlers.
        if (reviewForm && reviewsContainer) {
            reviewForm.addEventListener("submit", event => {
                event.preventDefault();
                if (typeof window.showLoader === "function") window.showLoader();

                const formData = new FormData(reviewForm);
                const csrf = reviewForm.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
                fetch(reviewForm.action, {
                    method: "POST",
                    body: formData,
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": csrf,
                    },
                })
                    .then(async response => {
                        const isJson = response.headers.get("content-type")?.includes("application/json");
                        const data = isJson ? await response.json() : null;
                        if (!response.ok) throw new Error(data?.message || "خطایی در پردازش درخواست رخ داد.");
                        return data;
                    })
                    .then(data => {
                        if (!data?.success) {
                            showReviewMessage(data?.message || "خطایی رخ داد.", true);
                            return;
                        }
                        reviewsContainer.innerHTML = data.reviews_html;
                        if (reviewCountBadge) reviewCountBadge.textContent = data.review_count + " نظر";
                        reviewForm.reset();
                        setRating(5);
                        showReviewMessage(data.message || "نظر شما با موفقیت ثبت شد.", false);
                    })
                    .catch(error => showReviewMessage(error.message || "در ثبت نظر مشکلی پیش آمد.", true))
                    .finally(() => {
                        if (typeof window.hideLoader === "function") window.hideLoader();
                    });
            });
        }

        let modalZoom = 1;
        function changeImage(url, selectedButton) {
            if (!mainImage || !url) return;
            mainImage.src = url;
            mainImage.dataset.originalSrc = url;
            mainImage.style.transform = "scale(1)";
            mainImage.style.transformOrigin = "center";
            thumbnailButtons.forEach(button => {
                button.classList.remove("border-indigo-600", "border-2");
                button.classList.add("border-gray-200", "border");
            });
            selectedButton.classList.remove("border-gray-200", "border");
            selectedButton.classList.add("border-indigo-600", "border-2");
        }
        thumbnailButtons.forEach(button => button.addEventListener("click", () => {
            changeImage(button.dataset.image, button);
        }));

        if (zoomStage && mainImage) {
            zoomStage.addEventListener("mousemove", event => {
                if (window.innerWidth < 1024) return;
                const rect = zoomStage.getBoundingClientRect();
                const x = ((event.clientX - rect.left) / rect.width) * 100;
                const y = ((event.clientY - rect.top) / rect.height) * 100;
                mainImage.style.transformOrigin = `${x}% ${y}%`;
                mainImage.style.transform = "scale(1.8)";
            });
            zoomStage.addEventListener("mouseleave", () => {
                mainImage.style.transform = "scale(1)";
                mainImage.style.transformOrigin = "center";
            });
        }

        function updateModalZoom() {
            if (modalImage) modalImage.style.transform = `scale(${modalZoom})`;
        }
        function openImageModal() {
            if (!imageModal || !modalImage || !mainImage) return;
            modalImage.src = mainImage.src;
            modalZoom = 1;
            updateModalZoom();
            imageModal.classList.remove("hidden");
            imageModal.classList.add("flex");
            document.body.classList.add("overflow-hidden");
        }
        function closeImageModal() {
            if (!imageModal) return;
            imageModal.classList.add("hidden");
            imageModal.classList.remove("flex");
            document.body.classList.remove("overflow-hidden");
            modalZoom = 1;
            updateModalZoom();
        }
        if (openModalButton) openModalButton.addEventListener("click", openImageModal);
        if (zoomStage) zoomStage.addEventListener("click", event => {
            if (event.target instanceof Element && event.target.closest("#open-image-modal")) return;
            openImageModal();
        });
        if (closeModalButton) closeModalButton.addEventListener("click", closeImageModal);
        if (zoomInButton) zoomInButton.addEventListener("click", () => {
            modalZoom = Math.min(modalZoom + 0.25, 3);
            updateModalZoom();
        });
        if (zoomOutButton) zoomOutButton.addEventListener("click", () => {
            modalZoom = Math.max(modalZoom - 0.25, 0.5);
            updateModalZoom();
        });
        if (zoomResetButton) zoomResetButton.addEventListener("click", () => {
            modalZoom = 1;
            updateModalZoom();
        });
        if (imageModal) imageModal.addEventListener("click", event => {
            if (event.target === imageModal) closeImageModal();
        });
        document.addEventListener("keydown", event => {
            if (event.key === "Escape") closeImageModal();
        });

        function getCookie(name) {
            const cookies = document.cookie ? document.cookie.split(";") : [];
            for (const item of cookies) {
                const cookie = item.trim();
                if (cookie.startsWith(`${name}=`)) {
                    return decodeURIComponent(cookie.substring(name.length + 1));
                }
            }
            return null;
        }
        function showWishlistMessage(message, isError) {
            if (!wishlistMessage) return;
            wishlistMessage.textContent = message;
            wishlistMessage.classList.remove("hidden", "text-emerald-600", "text-rose-600");
            wishlistMessage.classList.add(isError ? "text-rose-600" : "text-emerald-600");
        }
        if (wishlistButton) {
            wishlistButton.addEventListener("click", async () => {
                wishlistButton.disabled = true;
                try {
                    const response = await fetch(wishlistButton.dataset.endpoint, {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {
                            "X-CSRFToken": getCookie("csrftoken") || "",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    });
                    const data = await response.json();
                    if (!response.ok || !data.success) {
                        showWishlistMessage(data.message || "خطا در تغییر علاقه مندی.", true);
                        return;
                    }
                    const icon = wishlistButton.querySelector("i");
                    if (icon) {
                        icon.classList.toggle("fa-solid", data.is_wishlisted);
                        icon.classList.toggle("fa-regular", !data.is_wishlisted);
                        icon.classList.toggle("text-rose-500", data.is_wishlisted);
                        icon.classList.toggle("text-gray-400", !data.is_wishlisted);
                    }
                    showWishlistMessage(data.message, false);
                } catch (error) {
                    showWishlistMessage("ارتباط با سرور برقرار نشد.", true);
                } finally {
                    wishlistButton.disabled = false;
                }
            });
        }
    });
})();
