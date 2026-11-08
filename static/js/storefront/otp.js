(() => {
        "use strict";
let currentOtpPhone = "";
    let timerInterval = null;
    const modal = document.getElementById("otp-modal");
    if (!modal) return;

    const ENDPOINT_SEND = modal.dataset.sendEndpoint || "";
    const ENDPOINT_VERIFY = modal.dataset.verifyEndpoint || "";
    let previousFocus = null;
    const modalContent = document.getElementById("otp-modal-content");
    const step1 = document.getElementById("otp-step-1");
    const step2 = document.getElementById("otp-step-2");
    const phoneInput = document.getElementById("otp-phone");
    const displayPhone = document.getElementById("display-phone");
    const alertBox = document.getElementById("otp-alert");
    const digitInputs = document.querySelectorAll(".otp-digit");
    const btnSend = document.getElementById("btn-send-otp");
    const btnVerify = document.getElementById("btn-verify-otp");
    const btnResend = document.getElementById("btn-resend");
    const timerContainer = document.getElementById("otp-timer-container");
    const timerElement = document.getElementById("otp-timer");

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== "") {
            const cookies = document.cookie.split(";");
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + "=")) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    function toEnglishDigits(str) {
        if (!str) return "";
        return str.replace(/[۰-۹]/g, d => "۰۱۲۳۴۵۶۷۸۹".indexOf(d))
                  .replace(/[٠-٩]/g, d => "٠١٢٣٤٥٦٧٨٩".indexOf(d));
    }

    function showAlert(message, isError = true) {
        alertBox.textContent = message;
        alertBox.className = "mt-4 p-3 rounded-xl text-sm transition duration-200 text-center " + 
            (isError ? "bg-red-50 text-red-700 border border-red-200" : "bg-green-50 text-green-700 border border-green-200");
        alertBox.classList.remove("hidden");
    }

    function clearAlert() {
        alertBox.textContent = "";
        alertBox.classList.add("hidden");
    }

    window.openOtpModal = function() {
        previousFocus = document.activeElement;
        modal.setAttribute("aria-hidden", "false");
        clearAlert();
        goToStep1();
        modal.classList.remove("hidden");
        setTimeout(() => {
            modalContent.classList.remove("scale-95", "opacity-0");
            modalContent.classList.add("scale-100", "opacity-100");
            phoneInput.focus();
        }, 10);
    };

    window.closeOtpModal = function() {
        clearInterval(timerInterval);
        modal.setAttribute("aria-hidden", "true");
        modalContent.classList.remove("scale-100", "opacity-100");
        modalContent.classList.add("scale-95", "opacity-0");
        setTimeout(() => {
            modal.classList.add("hidden");
            if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
        }, 250);
    };

    window.goToStep1 = function() {
        clearInterval(timerInterval);
        step2.classList.add("hidden");
        step1.classList.remove("hidden");
        clearAlert();
        phoneInput.focus();
    };

    function goToStep2(phone, cooldown) {
        currentOtpPhone = phone;
        displayPhone.textContent = phone;
        step1.classList.add("hidden");
        step2.classList.remove("hidden");
        clearAlert();
        digitInputs.forEach(input => input.value = "");
        if (digitInputs.length > 0) digitInputs[0].focus();
        startTimer(cooldown || 120);
    }

    function startTimer(seconds) {
        clearInterval(timerInterval);
        let timeLeft = parseInt(seconds, 10) || 120;
        btnResend.classList.add("hidden");
        timerContainer.classList.remove("hidden");

        function update() {
            const m = String(Math.floor(timeLeft / 60)).padStart(2, "0");
            const s = String(timeLeft % 60).padStart(2, "0");
            timerElement.textContent = `${m}:${s}`;

            if (timeLeft <= 0) {
                clearInterval(timerInterval);
                timerContainer.classList.add("hidden");
                btnResend.classList.remove("hidden");
            }
            timeLeft--;
        }

        update();
        timerInterval = setInterval(update, 1000);
    }

    // مدیریت فوکوس و رفتارهای کیبورد در ۶ باکس
    digitInputs.forEach((input, index) => {
        input.addEventListener("input", (e) => {
            const val = toEnglishDigits(e.target.value).replace(/\D/g, "");
            e.target.value = val ? val[val.length - 1] : "";

            if (e.target.value && index < digitInputs.length - 1) {
                digitInputs[index + 1].focus();
            }

            const currentCode = Array.from(digitInputs).map(i => i.value).join("");
            if (currentCode.length === digitInputs.length) {
                handleVerifyOtp();
            }
        });

        input.addEventListener("keydown", (e) => {
            if (e.key === "Backspace" && !input.value && index > 0) {
                e.preventDefault();
                digitInputs[index - 1].focus();
            } else if (e.key === "ArrowLeft" && index > 0) {
                e.preventDefault();
                digitInputs[index - 1].focus();
            } else if (e.key === "ArrowRight" && index < digitInputs.length - 1) {
                e.preventDefault();
                digitInputs[index + 1].focus();
            } else if (e.key === "Enter") {
                handleVerifyOtp();
            }
        });
        

        input.addEventListener("paste", (e) => {
            e.preventDefault();
            const pasteData = toEnglishDigits((e.clipboardData || window.clipboardData).getData("text")).replace(/\D/g, "");
            if (!pasteData) return;

            const digits = pasteData.slice(0, digitInputs.length).split("");
            digits.forEach((d, i) => {
                digitInputs[i].value = d;
            });

            const nextFocus = Math.min(digits.length, digitInputs.length - 1);
            digitInputs[nextFocus].focus();

            if (digits.length === digitInputs.length) {
                handleVerifyOtp();
            }
        });
    });

    window.handleSendOtp = async function() {
        const phone = toEnglishDigits(phoneInput.value).trim() || currentOtpPhone;
        clearAlert();

        if (!/^09\d{9}$/.test(phone)) {
            showAlert("لطفاً یک شماره موبایل معتبر ۱۱ رقمی وارد کنید (مثال: 09123456789).");
            phoneInput.focus();
            return;
        }

        btnSend.disabled = true;
        btnSend.classList.add("opacity-60", "cursor-not-allowed");
        const originalText = btnSend.innerHTML;
        btnSend.innerHTML = "<span>در حال ارسال...</span>";

        try {
            const response = await fetch(ENDPOINT_SEND, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken"),
                    "X-Requested-With": "XMLHttpRequest"
                },
                body: JSON.stringify({ phone: phone })
            });

            const data = await response.json();
            if (response.ok && (data.success === true || data.status === "success" || !data.error)) {
                goToStep2(phone, data.cooldown || 120);
                showAlert(data.message || "کد تأیید ارسال شد.", false);
            }  
            else {
                showAlert(data.message || "خطا در ارسال کد تأیید.");
            }
        } catch (err) {
            showAlert("خطا در برقراری ارتباط با سرور.");
        } finally {
            btnSend.disabled = false;
            btnSend.classList.remove("opacity-60", "cursor-not-allowed");
            btnSend.innerHTML = originalText;
        }
    };

    window.handleVerifyOtp = async function() {
        const code = Array.from(digitInputs).map(i => toEnglishDigits(i.value)).join("");
        clearAlert();

        if (code.length !== digitInputs.length) {
            showAlert(`لطفاً کد تأیید ${digitInputs.length} رقمی را به طور کامل وارد کنید.`);
            return;
        }

        btnVerify.disabled = true;
        btnVerify.classList.add("opacity-60", "cursor-not-allowed");
        const originalText = btnVerify.innerHTML;
        btnVerify.innerHTML = "<span>در حال اعتبارسنجی...</span>";

        const urlParams = new URLSearchParams(window.location.search);
        const nextUrl = urlParams.get("next") || window.location.pathname;

        try {
            const response = await fetch(ENDPOINT_VERIFY, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken"),
                    "X-Requested-With": "XMLHttpRequest"
                },
                body: JSON.stringify({
                    phone: currentOtpPhone,
                    code: code,
                    next: nextUrl
                })
            });

            const data = await response.json();

            if (response.ok && (data.success === true || data.status === "success" || !data.error)) {
                showAlert(data.message || "ورود موفقیت‌آمیز بود. در حال انتقال...", false);
                setTimeout(() => {
                    window.location.href = data.redirect_url || nextUrl || "/";
                }, 600);
            } else {
                showAlert(data.message || "کد تأیید نامعتبر است.");
                digitInputs.forEach(i => i.value = "");
                digitInputs[0].focus();
            }
        } catch (err) {
            showAlert("خطا در برقراری ارتباط با سرور.");
        } finally {
            btnVerify.disabled = false;
            btnVerify.classList.remove("opacity-60", "cursor-not-allowed");
            btnVerify.innerHTML = originalText;
        }
    };

    // اینتر در اینپوت شماره
    phoneInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            handleSendOtp();
        }
    });

    document.addEventListener("click", (event) => {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) return;

        if (target.closest("[data-otp-open]")) {
            event.preventDefault();
            window.openOtpModal();
            return;
        }

        const action = target.closest("[data-otp-action]");
        if (!action) return;

        switch (action.dataset.otpAction) {
            case "close":
                window.closeOtpModal();
                break;
            case "send":
                window.handleSendOtp();
                break;
            case "verify":
                window.handleVerifyOtp();
                break;
            case "change-phone":
                window.goToStep1();
                break;
        }
    });

    modal.addEventListener("click", (event) => {
        if (event.target === modal) window.closeOtpModal();
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.classList.contains("hidden")) window.closeOtpModal();
    });

    if (document.querySelector("[data-otp-auto-open]")) {
        window.openOtpModal();
    }
})();
