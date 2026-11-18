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

DATABASE_URL = os.environ.get("DATABASE_URL")

# Pre-load settings from database if DATABASE_URL is available
_db_env = {}
if DATABASE_URL:
    try:
        import dj_database_url
        parsed_db = dj_database_url.parse(DATABASE_URL)
        if parsed_db.get("ENGINE") == "django.db.backends.mysql":
            import certifi
            import pymysql
            _conn = pymysql.connect(
                host=parsed_db.get("HOST", "127.0.0.1"),
                port=int(parsed_db.get("PORT", 3306)),
                user=parsed_db.get("USER", ""),
                password=parsed_db.get("PASSWORD", ""),
                database=parsed_db.get("NAME", ""),
                ssl_ca=certifi.where(),
                ssl_verify_cert=True,
                ssl_verify_identity=True,
                connect_timeout=5,
            )
            with _conn.cursor() as _cur:
                _cur.execute("SELECT k, v FROM settings;")
                for _k, _v in _cur.fetchall():
                    if isinstance(_v, str):
                        try:
                            import json
                            _db_env[_k] = json.loads(_v)
                        except Exception:
                            _db_env[_k] = _v
                    else:
                        _db_env[_k] = _v
            _conn.close()
    except Exception:
        pass

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    _db_env.get("django_secret_key", "l5Q6Kn72D0ws4pa-0Lq0Cz3ZSWaTbNKni_uMTs17eLXyEkuKtj31iHUt4Tive8ejulo")
)

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("true", "1", "yes")

APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", _db_env.get("app_public_url", "http://localhost:8080")).rstrip("/")

# Always permit onrender and local origins
ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    "https://*.onrender.com",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
if APP_PUBLIC_URL and APP_PUBLIC_URL not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(APP_PUBLIC_URL)

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
                "dashboard.context_processors.system_status",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# Default to SQLite for local tests if DATABASE_URL is not set
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    db_config = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=60,
        conn_health_checks=True,
    )
    if db_config.get("ENGINE") == "django.db.backends.mysql":
        import certifi
        db_config.setdefault("OPTIONS", {})
        db_config["OPTIONS"]["ssl"] = {"ca": certifi.where()}
        db_config["OPTIONS"]["charset"] = "utf8mb4"
    DATABASES = {"default": db_config}
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

# Admin & Super Admin Auth
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", _db_env.get("admin_email", "admin@gmail.com")).strip()
SUPER_EMAIL = os.environ.get("SUPER_EMAIL", _db_env.get("super_email", "super@gmail.com")).strip()
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", _db_env.get("admin_password_hash", "$2b$12$kM8Hcgv4ma6M7dmDW9iAjuizgJhH.X7zpG2Xd.qhr0fNuGii9rb2q")).strip()

# Encryption Key (Fernet)
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", _db_env.get("encryption_key", "UjHfBEKKddZkJCYod_IaeymLqn3BNV66OBrkwrF0Hxc=")).strip()

# Scheduler
RUN_SCHEDULER = str(os.environ.get("RUN_SCHEDULER", _db_env.get("run_scheduler", "1"))).lower() in ("1", "true", "yes")

# Storage Backend: "local" | "cloudinary" | "supabase"
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", _db_env.get("storage_backend", "cloudinary")).lower().strip()
LOCAL_INBOX_DIR = Path(os.environ.get("LOCAL_INBOX_DIR", str(BASE_DIR / "inbox")))
YOUTUBE_NATIVE_SCHEDULE = str(os.environ.get("YOUTUBE_NATIVE_SCHEDULE", _db_env.get("youtube_native_schedule", "true"))).lower() in ("1", "true", "yes")

# Cloudinary
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", _db_env.get("cloudinary_cloud_name", "dyqlcy4nz")).strip()
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", _db_env.get("cloudinary_api_key", "787573633254749")).strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", _db_env.get("cloudinary_api_secret", "Y_-le9zHMGjn9zca9lVacU5HuRo")).strip()

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", _db_env.get("supabase_url", "https://twluxyqbhtqwshiqclec.supabase.co")).rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", _db_env.get("supabase_service_role_key", "")).strip()
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", _db_env.get("supabase_bucket", "shorts")).strip()
STORAGE_INBOX_PREFIX = os.environ.get("STORAGE_INBOX_PREFIX", _db_env.get("storage_inbox_prefix", "inbox/")).strip()
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", _db_env.get("max_file_mb", 50)))

# Google / YouTube
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", _db_env.get("google_client_id", "")).strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", _db_env.get("google_client_secret", "")).strip()
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", _db_env.get("google_redirect_uri", f"{APP_PUBLIC_URL}/accounts/youtube/callback")
).strip()

# Meta / Instagram / Facebook
META_APP_ID = os.environ.get("META_APP_ID", _db_env.get("meta_app_id", "1152434697483892")).strip()
META_APP_SECRET = os.environ.get("META_APP_SECRET", _db_env.get("meta_app_secret", "2dde6ef488d3aa756639d078702abc24")).strip()
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", _db_env.get("graph_api_version", "v27.0")).strip()

# SMTP
SMTP_HOST = os.environ.get("SMTP_HOST", _db_env.get("smtp_host", "smtp.gmail.com")).strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", _db_env.get("smtp_port", 587)))
SMTP_USER = os.environ.get("SMTP_USER", _db_env.get("smtp_user", "idfake00420@gmail.com")).strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", _db_env.get("smtp_password", "iommwsxfojlznsbs")).strip()
SMTP_FROM = os.environ.get("SMTP_FROM", _db_env.get("smtp_from", "idfake00420@gmail.com")).strip()
SMTP_STARTTLS = str(os.environ.get("SMTP_STARTTLS", _db_env.get("smtp_starttls", "true"))).lower() in ("true", "1", "yes")

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
