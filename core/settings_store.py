"""
Settings store for Shorts Auto-Poster with defaults, DB-backed credentials, and upsert helpers.
Supports storing all API keys (Cloudinary, Supabase, SMTP, YouTube/Google) directly in the database.
"""
from typing import Any, Dict
from django.conf import settings as django_settings
from core.models import Setting

DEFAULTS: Dict[str, Any] = {
    "default_caption": {"title": "", "description": "", "hashtags": []},
    "caption_yt": None,
    "caption_ig": None,
    "caption_fb": None,
    "post_times": ["10:00", "14:00", "19:00"],
    "tz": "Asia/Kolkata",
    "max_per_day_yt": 6,
    "max_per_day_ig": 10,
    "max_per_day_fb": 10,
    "approval_timeout_min": 10,
    "retention_days": 7,
    "storage_limit_gb": 1,
    "poll_interval_min": 5,
    "paused": False,
    "paused_yt": False,
    "paused_ig": False,
    "paused_fb": False,
    "notify_enabled": True,
    "notify_email": "",
    "youtube_privacy": "public",
}

CONFIG_KEY_MAPPING: Dict[str, str] = {
    # Cloudinary
    "cloudinary_cloud_name": "CLOUDINARY_CLOUD_NAME",
    "cloudinary_api_key": "CLOUDINARY_API_KEY",
    "cloudinary_api_secret": "CLOUDINARY_API_SECRET",
    # Supabase
    "supabase_url": "SUPABASE_URL",
    "supabase_service_role_key": "SUPABASE_SERVICE_ROLE_KEY",
    "supabase_bucket": "SUPABASE_BUCKET",
    # SMTP
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "smtp_from": "SMTP_FROM",
    "smtp_starttls": "SMTP_STARTTLS",
    # YouTube / Google OAuth
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
    "google_redirect_uri": "GOOGLE_REDIRECT_URI",
    # Meta / Facebook / Instagram
    "meta_app_id": "META_APP_ID",
    "meta_app_secret": "META_APP_SECRET",
    "graph_api_version": "GRAPH_API_VERSION",
    # Core Auth & Security
    "django_secret_key": "DJANGO_SECRET_KEY",
    "encryption_key": "ENCRYPTION_KEY",
    "admin_password_hash": "ADMIN_PASSWORD_HASH",
    "admin_email": "ADMIN_EMAIL",
    "super_email": "SUPER_EMAIL",
    "shutdown_enabled": "SHUTDOWN_ENABLED",
}


def get_setting(key: str, default: Any = None) -> Any:
    """Retrieve setting value by key, falling back to DEFAULTS, then default."""
    try:
        obj = Setting.objects.filter(k=key).first()
        if obj is not None:
            return obj.v
    except Exception:
        pass
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


def get_config_value(key: str, default: Any = None) -> Any:
    """
    Retrieve configuration value:
    1. Checks DB `Setting` table (configured via UI).
    2. If not found or empty, checks django_settings / .env fallback.
    3. Falls back to DEFAULTS or provided default.
    """
    try:
        obj = Setting.objects.filter(k=key).first()
        if obj is not None and obj.v not in (None, ""):
            return obj.v
    except Exception:
        pass

    env_attr = CONFIG_KEY_MAPPING.get(key, key.upper())
    if hasattr(django_settings, env_attr):
        val = getattr(django_settings, env_attr)
        if val not in (None, ""):
            return val

    if key in DEFAULTS:
        return DEFAULTS[key]

    return default


def set_setting(key: str, value: Any) -> Setting:
    """Upsert setting value."""
    obj, _ = Setting.objects.update_or_create(k=key, defaults={"v": value})
    return obj


def get_all_settings() -> Dict[str, Any]:
    """Retrieve all settings combined with defaults and mapped credentials."""
    res = dict(DEFAULTS)
    for db_key, env_attr in CONFIG_KEY_MAPPING.items():
        res[db_key] = getattr(django_settings, env_attr, "")

    try:
        for item in Setting.objects.all():
            res[item.k] = item.v
    except Exception:
        pass
    return res


def sync_env_to_db() -> None:
    """Import credentials from .env / django_settings into DB Setting table if not present."""
    for db_key, env_attr in CONFIG_KEY_MAPPING.items():
        if hasattr(django_settings, env_attr):
            val = getattr(django_settings, env_attr)
            if val not in (None, ""):
                try:
                    if not Setting.objects.filter(k=db_key).exists():
                        set_setting(db_key, val)
                except Exception:
                    pass


def init_default_settings() -> None:
    """Ensure all default settings exist in the database and sync env keys."""
    for k, v in DEFAULTS.items():
        try:
            if not Setting.objects.filter(k=k).exists():
                Setting.objects.create(k=k, v=v)
        except Exception:
            pass
    sync_env_to_db()
    for k, v in DEFAULTS.items():
        if not Setting.objects.filter(k=k).exists():
            Setting.objects.create(k=k, v=v)
