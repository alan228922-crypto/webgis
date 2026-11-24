"""Offline Synthetic Data transport and the browser network boundary."""

from copy import deepcopy
from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
import json

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone


DEMO_PROJECT_SLUG = "synthetic-worksite"
SYNTHETIC_SOURCE = "Synthetic Data"
AREA = "synthetic-district"
COVERAGE_BBOX = (120.9, 24.8, 121.1, 25.1)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; frame-src 'none'; "
    "form-action 'self'; base-uri 'none'"
)
DISABLED_ROUTES = {
    "weather": "CWA",
    "current-alerts": "CWA",
    "project-weather-stations": "CWA",
    "project-radar": "CWA",
    "project-train-status": "TDX",
    "project-highway-traffic": "TDX",
    "highway-facility-tracks": "TDX",
    "project-cctv": "TDX",
    "project-dynamic-flood": "WRA",
    "project-dynamic-flood-image": "WRA",
}


class DemoBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.META.get("REMOTE_ADDR") != "127.0.0.1":
            return HttpResponseForbidden("The Synthetic Data demo is loopback-only.")
        response = self.get_response(request)
        response["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    def process_view(self, request, view, args, kwargs):
        route = request.resolver_match.url_name
        if route in DISABLED_ROUTES:
            payload = {"status": "disabled", "source": DISABLED_ROUTES[route], "records": [],
                       "fetched_at": None, "stale": False, "error": None,
                       "runtime_state": "disabled", "reason": "disabled", "cache": None}
            if route == "current-alerts":
                payload["alerts"] = []
            if route in {"project-highway-traffic", "highway-facility-tracks"}:
                payload.update(type="FeatureCollection", features=[])
            if route == "project-dynamic-flood":
                payload.update(overlay=None, service_state="disabled")
            return JsonResponse(payload, status=503 if route == "project-dynamic-flood-image" else 200)
        return None


def demo_context(request):
    return {"worksite_demo": True, "worksite_demo_scenario": settings.WORKSITE_DEMO_SCENARIO}


@lru_cache(maxsize=1)
def demo_fixtures():
    directory = settings.BASE_DIR / "docs" / "cases" / "synthetic-geological-screening"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    fixture = (directory / "fixtures.json").read_bytes()
    if hashlib.sha256(fixture).hexdigest() != manifest["sha256"]["fixtures.json"]:
        raise ValueError("Synthetic Data fixture does not match its manifest")
    return json.loads(fixture), manifest


def synthetic_area_record(metadata):
    return {
        "project_id": metadata["project_id"], "geometry_hash": metadata["geometry_hash"],
        "area": AREA, "source": SYNTHETIC_SOURCE, "coverage_bbox": COVERAGE_BBOX,
    }


def synthetic_geology(layer, scenario, bbox):
    fixture, manifest = demo_fixtures()
    empty = {"type": "FeatureCollection", "features": []}
    payload = empty if scenario == "empty" else deepcopy(fixture["layers"][layer])
    fetched_at = (
        timezone.now() - timedelta(minutes=10) if scenario == "stale" else
        datetime.fromisoformat(manifest["mock_fetched_at"])
    )
    coverage_known = False
    if bbox:
        west, south, east, north = map(float, bbox.split(","))
        min_x, min_y, max_x, max_y = COVERAGE_BBOX
        coverage_known = min_x <= west <= east <= max_x and min_y <= south <= north <= max_y
    evidence = {
        "source_id": SYNTHETIC_SOURCE, "dataset_version": manifest["dataset_version"],
        "source_published_at": manifest["source_published_at"],
        "complete": coverage_known and scenario != "unknown",
    }
    return {"payload": payload, "fetched_at": fetched_at.isoformat(), "evidence": evidence}
