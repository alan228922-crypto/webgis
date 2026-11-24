"""Local, synthetic-only settings. Start this profile with demo.py."""

from copy import deepcopy
from secrets import token_urlsafe

from .settings import *
from .demo_paths import DATABASE_FILE, validate_demo_directory


try:
    WORKSITE_DEMO_DIR = validate_demo_directory(os.environ["WORKSITE_DEMO_DIR"])
except (KeyError, OSError, ValueError) as exc:
    raise RuntimeError("Start with demo.py and use a private Demo workspace") from exc

WORKSITE_DEMO = True
WORKSITE_DEMO_SCENARIO = os.environ.get("WORKSITE_DEMO_SCENARIO", "normal")
if WORKSITE_DEMO_SCENARIO not in {"normal", "unknown", "timeout", "stale", "empty"}:
    raise RuntimeError("Unsupported Synthetic Data scenario")

# The launcher supplies a clean environment, and this profile never loads dotenv.
SECRET_KEY = token_urlsafe(48)
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1"]
CSRF_TRUSTED_ORIGINS = []
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_PROXY_SSL_HEADER = None
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": WORKSITE_DEMO_DIR / DATABASE_FILE}}
DATA_ROOT = WORKSITE_DEMO_DIR / "data"
DATA_CATALOG_DIR = DATA_ROOT / "catalog"
DATA_PROCESSED_DIR = DATA_ROOT / "processed"
OFFLINE_TILE_ROOT = WORKSITE_DEMO_DIR / "tiles"
SPATIAL_INDEX_PATH = DATA_ROOT / "risk.sqlite3"
WORKSITE_GEOLOGIC_SENSITIVE_VIEWPORT_INDEX_PATH = DATA_ROOT / "viewport.sqlite3"
GEOCODING_PROVIDER = "mock"
NOMINATIM_GEOCODING_URL = NLSC_GEOCODING_URL = NLSC_API_TOKEN = TGOS_API_KEY = TGOS_GEOCODING_URL = ""
CWA_API_KEY = TDX_CLIENT_ID = TDX_CLIENT_SECRET = ""
STATIC_ROOT = WORKSITE_DEMO_DIR / "static"
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
MIDDLEWARE = ["worksite_gis.demo.DemoBoundaryMiddleware", *MIDDLEWARE]
TEMPLATES = deepcopy(TEMPLATES)
TEMPLATES[0]["OPTIONS"]["context_processors"].append("worksite_gis.demo.demo_context")
