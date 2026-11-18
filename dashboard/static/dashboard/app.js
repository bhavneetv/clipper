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
        } else if (detail.message) {
            window.showToast(detail.message, detail.type || "info");
        } else if (detail.value) {
            if (typeof detail.value === "string") {
                window.showToast(detail.value, "info");
            } else if (detail.value.message) {
                window.showToast(detail.value.message, detail.value.type || "info");
            }
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
