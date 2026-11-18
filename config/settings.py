"""
Django settings for Shorts Auto-Poster.
"""
import os
import sys
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "insecure-default-key-change-in-production-only"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("true", "1", "yes")

APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "http://localhost:8000").rstrip("/")

allowed_hosts_str = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_str.split(",") if h.strip()]

csrf_origins_str = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
if csrf_origins_str:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in csrf_origins_str.split(",") if o.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [APP_PUBLIC_URL] if APP_PUBLIC_URL else []

# Application definition
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",
    "dashboard.apps.DashboardConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "dashboard.middleware.LoginRequiredMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "dashboard" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# Default to SQLite for local tests if DATABASE_URL is not set
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=60,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Timezone & Localization (Defaults to IST - Asia/Kolkata)
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True
APPEND_SLASH = False

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "dashboard" / "static",
]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Sessions (Database-backed)
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE_HOURS = int(os.environ.get("SESSION_COOKIE_AGE_HOURS", "24"))
SESSION_COOKIE_AGE = SESSION_COOKIE_AGE_HOURS * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False  # Allows HTMX / JS to read for hx-headers if needed

# Production Security Flags
TESTING = "test" in sys.argv
if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Single Admin Auth
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip()
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()

# Encryption Key (Fernet)
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "").strip()

# Scheduler
RUN_SCHEDULER = os.environ.get("RUN_SCHEDULER", "1").lower() in ("1", "true", "yes")

# Storage Backend: "local" | "cloudinary" | "supabase"
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower().strip()
LOCAL_INBOX_DIR = Path(os.environ.get("LOCAL_INBOX_DIR", str(BASE_DIR / "inbox")))
YOUTUBE_NATIVE_SCHEDULE = os.environ.get("YOUTUBE_NATIVE_SCHEDULE", "true").lower() in ("1", "true", "yes")

# Cloudinary
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "shorts")
STORAGE_INBOX_PREFIX = os.environ.get("STORAGE_INBOX_PREFIX", "inbox/")
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "50"))

# Google / YouTube
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", f"{APP_PUBLIC_URL}/accounts/youtube/callback"
)

# Meta / Instagram
META_APP_ID = os.environ.get("META_APP_ID", "").strip()
META_APP_SECRET = os.environ.get("META_APP_SECRET", "").strip()
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v21.0").strip()

# SMTP
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "true").lower() in ("true", "1", "yes")

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
