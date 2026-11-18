"""
Views for Application Settings and SMTP test notifications.
"""
import json
import zoneinfo
from django.conf import settings as django_settings
from django.contrib import messages
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from core.notifier import notify_test_email
from core.settings_store import get_all_settings, get_setting, set_setting
from core.storage import get_storage_backend, get_storage_usage_stats, purge_storage_files


@require_http_methods(["GET", "POST"])
def settings_view(request):
    """View and update application settings with validation."""
    current_settings = get_all_settings()
    errors = {}
    success_msg = None

    if request.method == "POST":
        # 1. Default Captions
        def_title = request.POST.get("default_title", "").strip()
        def_desc = request.POST.get("default_desc", "").strip()
        def_tags_str = request.POST.get("default_hashtags", "").strip()
        def_tags = [t.strip().lstrip("#") for t in def_tags_str.replace(",", " ").split() if t.strip()]

        default_cap = {
            "title": def_title,
            "description": def_desc,
            "hashtags": def_tags,
        }
        set_setting("default_caption", default_cap)

        # 2. Platform Captions
        yt_title = request.POST.get("yt_title", "").strip()
        yt_desc = request.POST.get("yt_desc", "").strip()
        yt_tags_str = request.POST.get("yt_hashtags", "").strip()
        if yt_title or yt_desc or yt_tags_str:
            yt_tags = [t.strip().lstrip("#") for t in yt_tags_str.replace(",", " ").split() if t.strip()]
            set_setting("caption_yt", {"title": yt_title, "description": yt_desc, "hashtags": yt_tags})
        else:
            set_setting("caption_yt", None)

        ig_desc = request.POST.get("ig_desc", "").strip()
        ig_tags_str = request.POST.get("ig_hashtags", "").strip()
        if ig_desc or ig_tags_str:
            ig_tags = [t.strip().lstrip("#") for t in ig_tags_str.replace(",", " ").split() if t.strip()]
            set_setting("caption_ig", {"title": "", "description": ig_desc, "hashtags": ig_tags})
        else:
            set_setting("caption_ig", None)

        fb_title = request.POST.get("fb_title", "").strip()
        fb_desc = request.POST.get("fb_desc", "").strip()
        fb_tags_str = request.POST.get("fb_hashtags", "").strip()
        if fb_title or fb_desc or fb_tags_str:
            fb_tags = [t.strip().lstrip("#") for t in fb_tags_str.replace(",", " ").split() if t.strip()]
            set_setting("caption_fb", {"title": fb_title, "description": fb_desc, "hashtags": fb_tags})
        else:
            set_setting("caption_fb", None)

        # 3. Post Times (validate HH:MM)
        post_times_raw = request.POST.get("post_times", "").strip()
        post_times = []
        for t in post_times_raw.replace(",", " ").split():
            t = t.strip()
            if t:
                parts = t.split(":")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    h, m = int(parts[0]), int(parts[1])
                    if 0 <= h <= 23 and 0 <= m <= 59:
                        post_times.append(f"{h:02d}:{m:02d}")
                    else:
                        errors["post_times"] = f"Invalid time: {t} (hour 0-23, min 0-59)"
                else:
                    errors["post_times"] = f"Invalid format: {t} (expected HH:MM)"

        if not errors.get("post_times") and post_times:
            set_setting("post_times", post_times)

        # 4. Timezone
        tz_input = request.POST.get("tz", "").strip()
        try:
            zoneinfo.ZoneInfo(tz_input)
            set_setting("tz", tz_input)
        except Exception:
            errors["tz"] = f"Unknown timezone: {tz_input}. Please use standard IANA format (e.g. Asia/Kolkata)."

        # 5. Integers (max_per_day_*, approval_timeout_min, retention_days, storage_limit_gb, poll_interval_min)
        try:
            val = int(request.POST.get("max_per_day_yt", 6))
            if val <= 0: raise ValueError()
            set_setting("max_per_day_yt", val)
        except ValueError:
            errors["max_per_day_yt"] = "Must be a positive integer"

        try:
            val = int(request.POST.get("max_per_day_ig", 10))
            if val <= 0: raise ValueError()
            set_setting("max_per_day_ig", val)
        except ValueError:
            errors["max_per_day_ig"] = "Must be a positive integer"

        try:
            val = int(request.POST.get("max_per_day_fb", 10))
            if val <= 0: raise ValueError()
            set_setting("max_per_day_fb", val)
        except ValueError:
            errors["max_per_day_fb"] = "Must be a positive integer"

        try:
            val = int(request.POST.get("approval_timeout_min", 10))
            if val < 0: raise ValueError()
            set_setting("approval_timeout_min", val)
        except ValueError:
            errors["approval_timeout_min"] = "Must be a non-negative integer (0 = no wait)"

        try:
            val = int(request.POST.get("retention_days", 7))
            if val <= 0: raise ValueError()
            set_setting("retention_days", val)
        except ValueError:
            errors["retention_days"] = "Must be a positive integer"

        try:
            val = int(request.POST.get("storage_limit_gb", 1))
            if val <= 0: raise ValueError()
            set_setting("storage_limit_gb", val)
        except ValueError:
            errors["storage_limit_gb"] = "Must be a positive integer"

        try:
            val = int(request.POST.get("poll_interval_min", 5))
            if val <= 0: raise ValueError()
            set_setting("poll_interval_min", val)
        except ValueError:
            errors["poll_interval_min"] = "Must be a positive integer"

        # 6. YouTube Privacy
        yt_priv = request.POST.get("youtube_privacy", "public").strip()
        if yt_priv in ("public", "unlisted", "private"):
            set_setting("youtube_privacy", yt_priv)

        # 7. Notifications
        notify_en = request.POST.get("notify_enabled") in ("on", "true", "1")
        set_setting("notify_enabled", notify_en)

        notify_email = request.POST.get("notify_email", "").strip()
        if notify_email:
            try:
                validate_email(notify_email)
                set_setting("notify_email", notify_email)
            except ValidationError:
                errors["notify_email"] = "Invalid email format"
        else:
            set_setting("notify_email", "")

        # 8. Storage Backend Selection
        storage_b = request.POST.get("storage_backend", "local").lower().strip()
        if storage_b in ("local", "cloudinary", "supabase"):
            set_setting("storage_backend", storage_b)

        # Cloudinary credentials
        c_name = request.POST.get("cloudinary_cloud_name", "").strip()
        c_key = request.POST.get("cloudinary_api_key", "").strip()
        c_sec = request.POST.get("cloudinary_api_secret", "").strip()
        if c_name: set_setting("cloudinary_cloud_name", c_name)
        if c_key: set_setting("cloudinary_api_key", c_key)
        if c_sec: set_setting("cloudinary_api_secret", c_sec)

        # Supabase credentials
        sb_url = request.POST.get("supabase_url", "").strip()
        sb_key = request.POST.get("supabase_service_role_key", "").strip()
        sb_bkt = request.POST.get("supabase_bucket", "").strip()
        if sb_url: set_setting("supabase_url", sb_url)
        if sb_key: set_setting("supabase_service_role_key", sb_key)
        if sb_bkt: set_setting("supabase_bucket", sb_bkt)

        # SMTP credentials
        s_host = request.POST.get("smtp_host", "").strip()
        s_port = request.POST.get("smtp_port", "").strip()
        s_user = request.POST.get("smtp_user", "").strip()
        s_pass = request.POST.get("smtp_password", "").strip()
        s_from = request.POST.get("smtp_from", "").strip()
        s_tls = request.POST.get("smtp_starttls") in ("on", "true", "1")
        if s_host: set_setting("smtp_host", s_host)
        if s_port: set_setting("smtp_port", int(s_port) if s_port.isdigit() else 587)
        if s_user: set_setting("smtp_user", s_user)
        if s_pass: set_setting("smtp_password", s_pass)
        if s_from: set_setting("smtp_from", s_from)
        set_setting("smtp_starttls", s_tls)

        # Google / YouTube OAuth credentials
        g_id = request.POST.get("google_client_id", "").strip()
        g_sec = request.POST.get("google_client_secret", "").strip()
        g_uri = request.POST.get("google_redirect_uri", "").strip()
        if g_id: set_setting("google_client_id", g_id)
        if g_sec: set_setting("google_client_secret", g_sec)
        if g_uri: set_setting("google_redirect_uri", g_uri)

        # Cron secret
        c_secret = request.POST.get("cron_secret", "").strip()
        if c_secret:
            set_setting("cron_secret", c_secret)

        current_settings = get_all_settings()
        if not errors:
            success_msg = "Settings saved successfully!"

    # Compute usage stats & cron trigger URL
    storage_stats = get_storage_usage_stats()
    cron_secret = get_setting("cron_secret", "shorts_cron_key_123456")
    base_url = getattr(django_settings, "APP_PUBLIC_URL", "http://localhost:8080").rstrip("/")
    cron_url = f"{base_url}/api/cron/tick?key={cron_secret}"

    context = {
        "s": current_settings,
        "errors": errors,
        "success_msg": success_msg,
        "storage_stats": storage_stats,
        "cron_url": cron_url,
    }
    return render(request, "dashboard/settings.html", context)


@require_POST
def test_notification_view(request):
    """Sends a test email to the configured notification address."""
    recipient = get_setting("notify_email", "").strip()
    if not recipient:
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": "Please configure a notification email first.", "type": "error"}})
        return res

    notify_test_email(recipient)
    res = HttpResponse(status=200)
    res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Test email sent to {recipient}!", "type": "success"}})
    return res


@require_POST
def storage_purge_view(request):
    """
    Purges video files stored in a specific storage backend.
    Parameters in POST:
        backend: 'local' | 'supabase' | 'cloudinary'
        mode: 'posted' (only posted videos) | 'all' (all files in that backend)
    """
    backend = request.POST.get("backend", "").strip().lower()
    mode = request.POST.get("mode", "posted").strip().lower()
    only_posted = (mode != "all")

    if backend not in ("local", "supabase", "cloudinary"):
        if request.headers.get("HX-Request") == "true":
            res = HttpResponse(status=400)
            res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Invalid storage backend: {backend}", "type": "error"}})
            return res
        return redirect("/settings")

    result = purge_storage_files(backend, only_posted=only_posted)
    target_label = "already-posted" if only_posted else "all"
    backend_display = {"local": "Local Disk", "supabase": "Supabase Storage", "cloudinary": "Cloudinary CDN"}.get(backend, backend)
    msg = f"Deleted {result['deleted_count']} {target_label} file(s) from {backend_display}! Freed {result['freed_mb']} MB."

    messages.success(request, msg)
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": msg, "type": "success"}})
        res["HX-Refresh"] = "true"
        return res

    return redirect("/settings")


@require_POST
def shutdown_toggle_view(request):
    """
    Toggles website shutdown / lock mode.
    Accessible only to Super Admin (super@gmail.com).
    When active, all visitors receive generic 'Error happened' response.
    """
    if request.session.get("user_role") != "super":
        messages.error(request, "Permission denied. Only Super Admin can toggle Emergency Shutdown.")
        return redirect(request.META.get("HTTP_REFERER", "/settings"))

    curr = bool(get_setting("shutdown_enabled", False))
    new_state = not curr
    set_setting("shutdown_enabled", new_state)

    if new_state:
        msg = "Emergency Shutdown ACTIVE: All visitors now see 'Error happened'."
        messages.warning(request, msg)
    else:
        msg = "Emergency Shutdown DEACTIVATED: Website access restored."
        messages.success(request, msg)

    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": msg, "type": "warning" if new_state else "success"}})
        res["HX-Refresh"] = "true"
        return res

    return redirect(request.META.get("HTTP_REFERER", "/settings"))
