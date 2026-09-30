"""Django settings for the restaurant operations app.

All deployment-specific values come from environment variables (see .env.example).
"""
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required")
    return value


def env_bool(name, default=False):
    return str(os.environ.get(name, str(default))).lower() in {"1", "true", "yes", "on"}


def load_dotenv(path):
    """Minimal .env loader so local development needs no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(BASE_DIR / ".env")

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-only-key" if DEBUG else None, required=not DEBUG)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
# Render (and similar hosts) tell the app its public hostname; trust it automatically.
_platform_host = env("RENDER_EXTERNAL_HOSTNAME", "")
if _platform_host and _platform_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_platform_host)
for _host in ALLOWED_HOSTS:
    if _host not in {"localhost", "127.0.0.1"} and not _host.startswith("."):
        _origin = f"https://{_host}"
        if _origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_origin)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "staff",
    "inventory",
    "checklists",
    "timeclock",
    "dashboard",
    "menu",
    "website",
]

MIDDLEWARE = [
    "core.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "core.middleware.RestaurantTimezoneMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.navigation",
            ],
        },
    },
]


def database_from_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must be a postgres:// URL")
    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "ATOMIC_REQUESTS": False,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
    # Pass connection options such as ?sslmode=require straight to the driver.
    options = dict(parse_qsl(parsed.query))
    if options:
        config["OPTIONS"] = options
    return config


DATABASES = {"default": database_from_url(env("DATABASE_URL", required=True))}

AUTH_USER_MODEL = "staff.Employee"
AUTHENTICATION_BACKENDS = ["staff.backends.EmployeeBackend"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "today"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# How long a one-time "set your password" link stays valid (seconds).
PASSWORD_RESET_TIMEOUT = int(env("PASSWORD_SETUP_LINK_TIMEOUT", 3 * 24 * 3600))

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_AGE = 12 * 3600
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HTTPS. Hosting platforms end TLS at their proxy and forward X-Forwarded-Proto.
if env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", not DEBUG and SESSION_COOKIE_SECURE)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # the platform's health check calls over plain HTTP
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", 0 if DEBUG else 60 * 60 * 24 * 30))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False  # a custom domain's other subdomains are not ours to decide
SECURE_REFERRER_POLICY = "same-origin"
# HSTS for subdomains/preload is left to the owner of a custom domain, who may run other sites on it.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("fi", "Suomi"), ("vi", "Tiếng Việt")]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = "UTC"  # storage and server time; display uses the restaurant's zone
DEFAULT_RESTAURANT_TIME_ZONE = env("RESTAURANT_TIME_ZONE", "Europe/Helsinki")

# Demo mode: for showing the app to people. On every start the database is wiped and refilled
# with the example restaurant, the demo accounts are listed on the sign-in page, and every page
# says it is a demo. Never enable it for a real restaurant.
DEMO_MODE = env_bool("DEMO_MODE", False)
DEMO_PASSWORD = env("DEMO_PASSWORD", "demo-bistro-2026")

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Compressed, cache-busting file names; served by WhiteNoise without a separate web server.
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Private uploads (checklist issue photos). Never served as public static/media.
PRIVATE_UPLOAD_ROOT = Path(env("PRIVATE_UPLOAD_ROOT", "private_uploads"))
if not PRIVATE_UPLOAD_ROOT.is_absolute():  # relative paths are relative to the project, not the shell
    PRIVATE_UPLOAD_ROOT = BASE_DIR / PRIVATE_UPLOAD_ROOT
ISSUE_PHOTO_MAX_BYTES = int(env("ISSUE_PHOTO_MAX_BYTES", 10 * 1024 * 1024))
# Public images (dish photos, logo, hero). Served by the app, not directly by the web server,
# so unpublished dishes' photos stay hidden.
PUBLIC_UPLOAD_ROOT = Path(env("PUBLIC_UPLOAD_ROOT", "public_uploads"))
if not PUBLIC_UPLOAD_ROOT.is_absolute():
    PUBLIC_UPLOAD_ROOT = BASE_DIR / PUBLIC_UPLOAD_ROOT
MENU_PHOTO_MAX_BYTES = int(env("MENU_PHOTO_MAX_BYTES", 10 * 1024 * 1024))
MENU_PHOTO_MAX_SIDE = 1600  # pixels; larger photos are scaled down on upload
DATA_UPLOAD_MAX_MEMORY_SIZE = max(ISSUE_PHOTO_MAX_BYTES, MENU_PHOTO_MAX_BYTES) + 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    # Never log request bodies: they may contain passwords.
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}

# Test runs: fast password hashing and no expected-403/404 log noise.
import sys  # noqa: E402

if len(sys.argv) > 1 and sys.argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["loggers"]["django.request"]["level"] = "CRITICAL"
    PRIVATE_UPLOAD_ROOT = BASE_DIR / ".test_private_uploads"
    PUBLIC_UPLOAD_ROOT = BASE_DIR / ".test_public_uploads"
    STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"
    SECURE_SSL_REDIRECT = False
