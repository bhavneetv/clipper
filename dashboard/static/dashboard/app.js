// CSRF helper for HTMX
document.addEventListener("DOMContentLoaded", function () {
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

    document.body.addEventListener("htmx:configRequest", function (evt) {
        const csrfToken = getCookie("csrftoken");
        if (csrfToken) {
            evt.detail.headers["X-CSRFToken"] = csrfToken;
        }
    });

    // Handle toast messages from server HX-Trigger
    document.body.addEventListener("showMessage", function (evt) {
        if (!window.showToast) return;
        const detail = evt.detail;
        if (!detail) return;
        if (typeof detail === "string") {
            window.showToast(detail, "info");
        } else if (typeof detail === "object") {
            const msg = detail.message || detail.text || (typeof detail.value === "string" ? detail.value : (detail.value && detail.value.message)) || detail.error || "";
            const typ = detail.type || (detail.value && detail.value.type) || "info";
            if (msg) {
                window.showToast(msg, typ);
            }
        }
    });

    // Handle HTMX server errors gracefully with toasts
    document.body.addEventListener("htmx:responseError", function (evt) {
        let msg = "Action failed (HTTP " + (evt.detail.xhr ? evt.detail.xhr.status : "Error") + ")";
        try {
            const data = JSON.parse(evt.detail.xhr.responseText);
            if (data.error) msg = data.error;
            else if (data.message) msg = data.message;
        } catch (e) {}
        if (window.showToast) {
            window.showToast(msg, "error");
        }
    });

    document.body.addEventListener("htmx:sendError", function () {
        if (window.showToast) {
            window.showToast("Network connection error. Server unreachable.", "error");
        }
    });
});

// Alpine Countdown Component
function countdownTimer(initialSeconds) {
    return {
        remaining: initialSeconds,
        timer: null,
        init() {
            if (this.remaining <= 0) return;
            this.timer = setInterval(() => {
                if (this.remaining > 0) {
                    this.remaining--;
                } else {
                    clearInterval(this.timer);
                }
            }, 1000);
        },
        destroy() {
            if (this.timer) clearInterval(this.timer);
        },
        get formatted() {
            if (this.remaining <= 0) return "00:00";
            const mins = Math.floor(this.remaining / 60);
            const secs = this.remaining % 60;
            return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
        }
    };
}
