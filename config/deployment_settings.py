"""Explicit deployment profile for isolated verification or an approved host."""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *


DEBUG = False
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Set a strong deployment SECRET_KEY.")

if not os.environ.get("DJANGO_ALLOWED_HOSTS") or "*" in ALLOWED_HOSTS or not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set explicit deployment DJANGO_ALLOWED_HOSTS.")

if os.environ.get("DATABASE_URL"):
    raise ImproperlyConfigured("Configure the deployed DB with DB_ENGINE and DB_* settings.")

if DB_ENGINE in {"postgres", "postgresql"}:
    if not all(DATABASES["default"].get(field) for field in ("NAME", "HOST", "USER")):
        raise ImproperlyConfigured("PostgreSQL requires DB_NAME, DB_HOST and DB_USER.")
    DATABASES["default"]["OPTIONS"] = {"connect_timeout": 3}

SECRET_KEY_FALLBACKS = []
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if any(not origin.startswith("https://") or "*" in origin for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Deployment CSRF origins must be explicit HTTPS origins.")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if os.environ.get("DJANGO_TRUST_HTTPS_PROXY") == "1" else None
)

STATIC_ROOT = Path(os.environ.get("WORKSITE_STATIC_ROOT", BASE_DIR / "staticfiles")).resolve()
protected = (DATA_ROOT.resolve(), OFFLINE_TILE_ROOT.resolve(), BASE_DIR.resolve())
if any(STATIC_ROOT == path or STATIC_ROOT in path.parents for path in protected) or any(
    path in STATIC_ROOT.parents for path in protected[:2]
):
    raise ImproperlyConfigured("Choose a dedicated static build output directory.")
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"stderr": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django": {"handlers": ["stderr"], "level": "WARNING", "propagate": False},
        "httpx": {"handlers": ["stderr"], "level": "WARNING", "propagate": False},
        "httpcore": {"handlers": ["stderr"], "level": "WARNING", "propagate": False},
    },
}
