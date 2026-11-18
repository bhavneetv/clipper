import time
import bcrypt
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from core.settings_store import get_config_value


def get_client_ip(request) -> str:
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


@require_http_methods(["GET", "POST"])
def login_view(request):
    """
    Single-admin login view.
    Validates against ADMIN_EMAIL and bcrypt ADMIN_PASSWORD_HASH.
    Rate-limits failed attempts: max 5 per 15 minutes per IP.
    """
    if request.session.get("is_authenticated"):
        return redirect("/")

    ip = get_client_ip(request)
    cache_key = f"login_attempts_{ip}"
    attempts = cache.get(cache_key, 0)
    error = None

    if attempts >= 5:
        error = "Too many failed login attempts. Please wait 15 minutes before trying again."
        return render(request, "dashboard/login.html", {"error": error, "rate_limited": True}, status=429)

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "").strip()

        admin_hash = str(get_config_value("admin_password_hash", getattr(settings, "ADMIN_PASSWORD_HASH", ""))).strip()

        admin_email = str(get_config_value("admin_email", getattr(settings, "ADMIN_EMAIL", "admin@gmail.com"))).strip().lower()
        super_email = str(get_config_value("super_email", getattr(settings, "SUPER_EMAIL", "super@gmail.com"))).strip().lower()

        valid_role = None
        if email in (super_email, "super@gmail.com"):
            valid_role = "super"
        elif email in (admin_email, "admin@gmail.com", "admin@example.com"):
            valid_role = "admin"

        is_valid = False
        if valid_role and admin_hash and password:
            try:
                # Compare password with bcrypt hash
                if bcrypt.checkpw(password.encode("utf-8"), admin_hash.encode("utf-8")):
                    is_valid = True
            except Exception:
                is_valid = False

        if is_valid:
            cache.delete(cache_key)
            request.session["is_authenticated"] = True
            request.session["admin_email"] = email
            request.session["user_role"] = valid_role
            return redirect("/")
        else:
            attempts += 1
            cache.set(cache_key, attempts, timeout=15 * 60)
            remaining = max(0, 5 - attempts)
            if remaining == 0:
                error = "Too many failed login attempts. Please wait 15 minutes before trying again."
                return render(request, "dashboard/login.html", {"error": error, "rate_limited": True}, status=429)
            else:
                error = f"Invalid email or password. ({remaining} attempts remaining)"

    return render(request, "dashboard/login.html", {"error": error, "rate_limited": False})


@require_http_methods(["POST"])
def logout_view(request):
    """Logs out admin and flushes session."""
    request.session.flush()
    return redirect("/login")
