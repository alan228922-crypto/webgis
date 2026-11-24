import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db import DatabaseError, connection
from django.http import JsonResponse


CORE_SCHEMA_PROBES = (
    "SELECT id FROM auth_user WHERE 1 = 0",
    "SELECT session_key FROM django_session WHERE 1 = 0",
    "SELECT geology_source_plan_id FROM worksite_gis_project WHERE 1 = 0",
    "SELECT lock_version, publication_event_id FROM worksite_gis_plan WHERE 1 = 0",
    "SELECT version FROM worksite_gis_planversion WHERE 1 = 0",
    "SELECT revision_id FROM worksite_gis_planworkflowevent WHERE 1 = 0",
)
REQUIRED_STATIC = (
    "home/js/map.js", "home/css/dashboard.css", "home/js/maintainer_plans.js",
)
DEMO_STATIC = (
    "home/vendor/leaflet/leaflet.js", "home/vendor/leaflet/leaflet.css",
    "home/vendor/geoman/leaflet-geoman.min.js", "home/vendor/geoman/leaflet-geoman.css",
)


@contextmanager
def _probe_cursor():
    if connection.vendor == "sqlite":
        name = str(connection.settings_dict["NAME"])
        if name == ":memory:" or (name.startswith("file:") and "mode=memory" in name):
            with connection.cursor() as cursor:
                yield cursor
            return
        # SQLite's default connect can create the file. The probe may only read it.
        uri = Path(name).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as probe, closing(probe.cursor()) as cursor:
            yield cursor
        return
    if connection.vendor != "postgresql":
        raise DatabaseError("Readiness probe requires a supported backend")
    probe = connection.copy()
    options = probe.settings_dict.setdefault("OPTIONS", {})
    options["connect_timeout"] = 2
    options["options"] = (options.get("options", "") + " -c statement_timeout=1000").strip()
    try:
        with probe.cursor() as cursor:
            yield cursor
    finally:
        probe.close()


def _static_ready():
    resources = REQUIRED_STATIC + (DEMO_STATIC if getattr(settings, "WORKSITE_DEMO", False) else ())
    if settings.DEBUG:
        return all(finders.find(resource) for resource in resources)
    root = settings.STATIC_ROOT
    return bool(root) and all((Path(root) / resource).is_file() for resource in resources)


def ready(request):
    checks = {"database": "unavailable", "schema": "unknown", "static": "unavailable"}
    try:
        with _probe_cursor() as cursor:
            cursor.execute("SELECT 1")
            checks["database"] = "available"
            checks["schema"] = "unavailable"
            for query in CORE_SCHEMA_PROBES:
                cursor.execute(query)
            checks["schema"] = "available"
    except (DatabaseError, sqlite3.Error, OSError):
        pass
    try:
        if _static_ready():
            checks["static"] = "available"
    except (OSError, ValueError):
        pass
    healthy = all(value == "available" for value in checks.values())
    response = JsonResponse({"status": "ready" if healthy else "not_ready", "checks": checks},
                            status=200 if healthy else 503)
    response["Cache-Control"] = "no-store"
    return response
