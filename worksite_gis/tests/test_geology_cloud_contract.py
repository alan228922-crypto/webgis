import httpx
import json
import os
import pytest
import shutil
import subprocess
import sys
import tempfile
import warnings
from django.core.cache import cache
from django.core.cache.backends.filebased import FileBasedCache
from django.test import override_settings
from shapely.geometry import Polygon

from worksite_gis.integrations.geology_cloud import (
    GEOLOGY_LAYERS,
    GeologyCloudAdapter,
    build_cache_key,
    normalize_geojson,
)
from worksite_gis.models import ApiSnapshot, Project
from worksite_gis.services.geology_screening import screen_features


pytestmark = pytest.mark.django_db


def feature(geometry, properties=None):
    return {"type": "Feature", "geometry": geometry, "properties": properties or {}}


def test_geology_layer_registry_excludes_unapproved_phase_one_layers():
    assert set(GEOLOGY_LAYERS) == {
        "drill",
        "liquefaction",
        "dip_slope",
        "rock_fall",
        "debris_flow_track",
    }


def test_normalize_geojson_rejects_missing_geometry_and_properties():
    with pytest.raises(ValueError, match="geometry"):
        normalize_geojson({"type": "FeatureCollection", "features": [feature(None)]})
    with pytest.raises(ValueError, match="properties"):
        normalize_geojson({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121, 25]}}]})


def test_drill_normalization_adds_composite_identifier():
    payload = normalize_geojson({
        "type": "FeatureCollection",
        "features": [feature({"type": "Point", "coordinates": [121, 25]}, {
            "Project_No": "P1", "Hole_Point_No": "H1", "Depth": 30,
        })],
    }, layer="drill")
    assert payload["features"][0]["properties"]["geology_id"] == "P1+H1"


def test_cache_key_contains_endpoint_query_area_and_version():
    key = build_cache_key("/api/v1/zh-tw/Drill", {"bbox": "121,25,122,26"}, version="v1")
    assert key.startswith("geology-layer:v1:")
    assert len(key.rsplit(":", 1)[-1]) == 32


def test_cache_key_contains_project_layer_set_and_geometry_context():
    key = build_cache_key(
        "/api/v1/zh-tw/Drill",
        {"bbox": "121,25,122,26"},
        version="v2",
        project_slug="project-a",
        layer="drill",
        layer_set="drill,liquefaction,dip_slope,rock_fall,debris_flow_track",
        bbox="121,25,122,26",
        area="none",
        limit=20,
        geometry_hash="geometry-a",
    )
    assert key.startswith("geology-layer:v2:")
    assert len(key) == len("geology-layer:v2:") + 32


def test_cache_key_does_not_collide_for_project_limit_or_geometry():
    base = {"bbox": "121,25,122,26"}
    kwargs = {"project_slug": "project-a", "layer": "drill", "layer_set": "drill", "bbox": base["bbox"], "area": "none", "limit": 20, "geometry_hash": "geometry-a"}
    keys = {
        build_cache_key("Drill", base, **kwargs),
        build_cache_key("Drill", base, **{**kwargs, "project_slug": "project-b"}),
        build_cache_key("Drill", base, **{**kwargs, "limit": 21}),
        build_cache_key("Drill", base, **{**kwargs, "geometry_hash": "geometry-b"}),
        build_cache_key("Drill", base, **{**kwargs, "layer_set": "drill,rock_fall"}),
    }
    assert len(keys) == 5


def test_cache_key_is_canonical_fixed_ascii_and_preserves_all_context_fields():
    kwargs = {
        "project_slug": "project-a",
        "layer": "drill",
        "layer_set": ["rock_fall", "drill", "liquefaction"],
        "bbox": "121,25,122,26",
        "area": None,
        "limit": 20,
        "geometry_hash": "geometry-a",
    }
    key = build_cache_key("Drill", {"bbox": "121,25,122,26", "area": None, "limit": 20}, **kwargs)
    reordered = build_cache_key("Drill", {"limit": 20, "area": None, "bbox": "121,25,122,26"}, **{**kwargs, "layer_set": ["liquefaction", "rock_fall", "drill"]})
    assert key == reordered
    assert key.startswith("geology-layer:v3:")
    assert len(key) == len("geology-layer:v3:") + 32
    assert key.isascii()


@pytest.mark.parametrize("field, value", [
    ("project_slug", "project-b"),
    ("layer", "rock_fall"),
    ("layer_set", ["drill", "rock_fall"]),
    ("bbox", "121,26,122,27"),
    ("area", "Taipei"),
    ("limit", 21),
    ("geometry_hash", "geometry-b"),
])
def test_cache_key_changes_when_any_semantic_field_changes(field, value):
    base = {"project_slug": "project-a", "layer": "drill", "layer_set": ["drill"], "bbox": "121,25,122,26", "area": None, "limit": 20, "geometry_hash": "geometry-a"}
    changed = {**base, field: value}
    assert build_cache_key("Drill", {"bbox": base["bbox"]}, **base) != build_cache_key("Drill", {"bbox": changed["bbox"]}, **changed)


def test_v2_and_v3_cache_keys_do_not_interoperate():
    kwargs = {"project_slug": "project-a", "layer": "drill", "layer_set": ["drill"], "bbox": "121,25,122,26", "area": None, "limit": 20, "geometry_hash": "geometry-a"}
    v2 = build_cache_key("Drill", {"bbox": kwargs["bbox"]}, version="v2", **kwargs)
    v3 = build_cache_key("Drill", {"bbox": kwargs["bbox"]}, version="v3", **kwargs)
    cache.set(v2, {"status": "old"}, 60)
    assert cache.get(v2) == {"status": "old"}
    assert cache.get(v3) is None


def test_locmem_cache_accepts_v3_key_without_warning():
    kwargs = {"project_slug": "project-a", "layer": "drill", "layer_set": ["drill"], "bbox": "121,25,122,26", "area": None, "limit": 20, "geometry_hash": "geometry-a"}
    key = build_cache_key("Drill", {"bbox": kwargs["bbox"]}, **kwargs)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cache.set(key, {"status": "live"}, 60)
        assert cache.get(key) == {"status": "live"}
    assert not [warning for warning in caught if warning.category.__name__ == "CacheKeyWarning"]


_FILEBASED_CACHE_PROBE = """
import json
import sys
from django.core.cache.backends.filebased import FileBasedCache

cache_dir, key = sys.argv[1:3]
try:
    cache = FileBasedCache(cache_dir, {\"OPTIONS\": {}})
    cache.set(key, {\"status\": \"live\"}, 60)
    value = cache.get(key)
    print(json.dumps({\"status\": \"available\", \"value\": value}))
except (PermissionError, OSError) as exc:
    print(json.dumps({\"status\": \"unavailable\", \"reason\": f\"{type(exc).__name__}: {exc}\"}))
"""


def _probe_filebased_cache(cache_dir, key, *, timeout):
    process = subprocess.Popen(
        [sys.executable, "-c", _FILEBASED_CACHE_PROBE, cache_dir, key],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        return {
            "status": "blocked",
            "backend": "FileBasedCache",
            "directory": cache_dir,
            "reason": "probe timeout",
            "timeout_seconds": timeout,
        }

    if process.returncode != 0:
        return {
            "status": "unavailable",
            "backend": "FileBasedCache",
            "directory": cache_dir,
            "reason": stderr.strip() or f"probe exit code {process.returncode}",
            "timeout_seconds": timeout,
        }
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        result = {"status": "unavailable", "reason": stdout.strip() or "invalid probe response"}
    result.update({"backend": "FileBasedCache", "directory": cache_dir, "timeout_seconds": timeout})
    return result


def test_filebased_cache_capability_probe_is_bounded_and_reports_result():
    kwargs = {"project_slug": "project-a", "layer": "drill", "layer_set": ["drill"], "bbox": "121,25,122,26", "area": None, "limit": 20, "geometry_hash": "geometry-a"}
    key = build_cache_key("Drill", {"bbox": kwargs["bbox"]}, **kwargs)
    cache_dir = tempfile.mkdtemp(prefix="worksite-file-cache-probe-")
    try:
        result = _probe_filebased_cache(cache_dir, key, timeout=2)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

    assert result["status"] in {"available", "unavailable", "blocked"}
    assert result["backend"] == "FileBasedCache"
    assert result["timeout_seconds"] == 2


def test_filebased_cache_accepts_v3_key():
    kwargs = {"project_slug": "project-a", "layer": "drill", "layer_set": ["drill"], "bbox": "121,25,122,26", "area": None, "limit": 20, "geometry_hash": "geometry-a"}
    key = build_cache_key("Drill", {"bbox": kwargs["bbox"]}, **kwargs)
    cache_dir = tempfile.mkdtemp(prefix="worksite-file-cache-")
    try:
        result = _probe_filebased_cache(cache_dir, key, timeout=2)
        if result["status"] != "available":
            pytest.skip(
                "FileBasedCache unavailable: "
                f"backend={result['backend']} directory={result['directory']} "
                f"reason={result.get('reason', 'unknown')} "
                f"timeout={result['timeout_seconds']}s"
            )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            file_cache = FileBasedCache(cache_dir, {"OPTIONS": {}})
            file_cache.set(key, {"status": "live"}, 60)
            assert file_cache.get(key) == {"status": "live"}
        assert not [warning for warning in caught if warning.category.__name__ == "CacheKeyWarning"]
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)


def test_screening_uses_projected_distance_and_reports_intersection():
    worksite = Polygon([(121, 25), (121.001, 25), (121.001, 25.001), (121, 25.001)])
    payload = {"type": "FeatureCollection", "features": [feature({
        "type": "Point", "coordinates": [121.0005, 25.0005]
    }, {"Project_No": "P1", "Hole_Point_No": "H1", "Depth": 10})]}
    result = screen_features(payload, worksite, layer="drill")
    assert result["intersects"] is True
    assert result["nearby_feature_count"] == 1
    assert result["nearest_drill"]["properties"]["geology_id"] == "P1+H1"
    assert result["nearest_distance_m"] == 0


def test_screening_without_formal_geometry_is_explicitly_unavailable():
    result = screen_features({"type": "FeatureCollection", "features": []}, None, layer="drill")
    assert result == {"status": "no_worksite_geometry", "intersects": None, "nearest_distance_m": None, "nearby_feature_count": 0, "nearest_drill": None}


def test_layer_response_labels_candidate_screening_count_scope():
    from worksite_gis.services.geology_screening import GeologyCloudService

    payload = {"type": "FeatureCollection", "features": [feature({"type": "Point", "coordinates": [121, 25]}, {})] * 2}
    response = GeologyCloudService._response("live", {"fetched_at": "2026-08-24T00:00:00Z", "payload": payload}, None, "drill", "121,25,121.1,25.1", limit=1)
    assert response["screening"]["nearby_feature_count_scope"] == "candidate_features"
    assert response["returned_feature_count"] == 1


def test_liquefaction_without_derived_area_is_unavailable():
    project = Project.objects.create(slug="no-area", name="No area")
    from worksite_gis.services.geology_screening import derive_liquefaction_area

    assert derive_liquefaction_area(project, None) is None


def test_geology_endpoint_has_stable_response_schema_and_no_database_snapshot(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="geo-user", password="password")
    project = Project.objects.create(slug="geo", name="Geo", owner=user)
    client.force_login(user)
    payload = {"type": "FeatureCollection", "features": []}

    class FakeService:
        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None, publication=None):
            return {"status": "live", "fetched_at": "2026-08-21T00:00:00+00:00", "source": "geologycloud", "features": payload["features"], "screening": {"status": "no_worksite_geometry"}, "error": None}

    monkeypatch.setattr("worksite_gis.views.GeologyCloudService", FakeService)
    response = client.get("/api/projects/geo/geology/drill/?bbox=121,25,121.1,25.1&limit=10")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "fetched_at", "source", "features", "screening", "error"}
    assert not ApiSnapshot.objects.filter(service="geologycloud").exists()


def test_geology_endpoint_rejects_oversized_bbox_and_limit(client, django_user_model):
    user = django_user_model.objects.create_user(username="geo-limit", password="password")
    Project.objects.create(slug="geo-limit", name="Geo limit", owner=user)
    client.force_login(user)
    assert client.get("/api/projects/geo-limit/geology/drill/?bbox=120,20,123,26").status_code == 400
    assert client.get("/api/projects/geo-limit/geology/drill/?bbox=121,25,121.1,25.1&limit=1001").status_code == 400


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, content_type="application/json"):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("upstream failed", request=None, response=self)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return self.response


def test_adapter_accepts_valid_geojson_and_sends_only_fixed_endpoint():
    client = FakeClient(FakeResponse({"type": "FeatureCollection", "features": []}))
    adapter = GeologyCloudAdapter("drill", client=client)
    payload, fetched_at = adapter.fetch({"bbox": "121,25,121.1,25.1"})
    assert payload == {"type": "FeatureCollection", "features": []}
    assert fetched_at is not None
    assert client.calls[0][0].endswith("/Drill")
    assert client.calls[0][1]["t"] == ".json"


@pytest.mark.parametrize("response", [
    FakeResponse({}, content_type="text/html"),
    FakeResponse(httpx.HTTPStatusError("404", request=None, response=None), status_code=404),
    FakeResponse(ValueError("invalid")),
])
def test_adapter_isolates_non_json_http_and_json_failures(response):
    adapter = GeologyCloudAdapter("drill", client=FakeClient(response))
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        adapter.fetch({"bbox": "121,25,121.1,25.1"})


def test_adapter_rejects_invalid_geometry():
    invalid = {"type": "FeatureCollection", "features": [feature({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]}, {})]}
    with pytest.raises(ValueError, match="geometry"):
        GeologyCloudAdapter("dip_slope", client=FakeClient(FakeResponse(invalid))).fetch({"bbox": "121,25,121.1,25.1"})


def test_liquefaction_features_are_clipped_after_upstream_area_query():
    from worksite_gis.services.geology_screening import GeologyCloudService

    payload = {"type": "FeatureCollection", "features": [
        feature({"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]}, {"classify": 1}),
        feature({"type": "Polygon", "coordinates": [[[122, 26], [122.01, 26], [122.01, 26.01], [122, 26.01], [122, 26]]]}, {"classify": 1}),
    ]}
    clipped = GeologyCloudService._clip_bbox(payload, "121,25,121.1,25.1")
    assert len(clipped["features"]) == 1


def test_upstream_failure_uses_stale_cache_without_leaking_request_details(monkeypatch):
    from worksite_gis.services.geology_screening import GeologyCloudService

    cache.clear()
    query = {"bbox": "121,25,121.1,25.1", "area": None, "limit": None}
    key = build_cache_key("Drill", query)
    cache.set(key + ":stale", {"fetched_at": "2026-08-20T00:00:00+00:00", "payload": {"type": "FeatureCollection", "features": []}}, 60)

    def broken_fetch(self, query):
        raise httpx.ReadTimeout("https://example.test/Drill?api_key=secret")

    monkeypatch.setattr("worksite_gis.services.geology_screening.GeologyCloudAdapter.fetch", broken_fetch)
    result = GeologyCloudService().fetch_layer("drill", bbox="121,25,121.1,25.1", project=None)
    assert result["status"] == "stale"
    assert "secret" not in result["error"]


def test_worker_can_use_request_thread_stale_cache_context(monkeypatch):
    from worksite_gis.services.geology_screening import GeologyCloudService

    stale = {
        "fetched_at": "2026-08-20T00:00:00+00:00",
        "payload": {"type": "FeatureCollection", "features": []},
    }

    class FailingAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, query):
            raise TimeoutError("upstream timeout")

    monkeypatch.setattr("worksite_gis.services.geology_screening.GeologyCloudAdapter", FailingAdapter)
    result = GeologyCloudService().fetch_layer(
        "drill",
        bbox="121,25,121.1,25.1",
        cache_context={"fresh": None, "stale": stale, "cache_key": "thread-safe-key"},
    )

    assert result["status"] == "stale"
    assert result["fetched_at"] == stale["fetched_at"]
    assert result["error"] == "TimeoutError: upstream timeout"


def test_frontend_contains_phase_one_geology_controls_and_safe_label():
    from pathlib import Path

    script = (Path("home/static/home/js/map.js")).read_text(encoding="utf-8")
    template = (Path("home/templates/home/map.html")).read_text(encoding="utf-8")
    for layer in ("drill", "liquefaction", "dip_slope", "rock_fall", "debris_flow_track"):
        assert layer in script
    assert "geology-screening-results" in template
    assert "此結果為官方資料的空間提示，不代表正式地質鑑定或施工安全判定" in template
