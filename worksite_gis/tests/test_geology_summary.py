import pytest
import time
import threading

from shapely.geometry import Polygon

from worksite_gis.models import Plan, Project


pytestmark = pytest.mark.django_db


def test_geology_summary_endpoint_returns_contract_for_project(client, django_user_model):
    user = django_user_model.objects.create_user(username="summary-user", password="secret")
    Project.objects.create(slug="summary-project", name="Summary project", owner=user)
    client.force_login(user)

    response = client.get(
        "/api/projects/summary-project/geology/summary/"
        "?bbox=121,25,121.1,25.1&limit=20"
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "project",
        "status",
        "generated_at",
        "cards",
        "layers",
        "actions",
        "limitations",
    }
    assert payload["project"] == {
        "slug": "summary-project",
        "name": "Summary project",
        "geometry_available": False,
    }
    assert payload["status"] == "unavailable"
    assert len(payload["layers"]) == 5


def test_geology_summary_endpoint_rejects_invalid_bbox(client, django_user_model):
    user = django_user_model.objects.create_user(username="summary-bbox", password="secret")
    Project.objects.create(slug="summary-bbox", name="Summary bbox", owner=user)
    client.force_login(user)

    response = client.get(
        "/api/projects/summary-bbox/geology/summary/"
        "?bbox=120,20,123,26&limit=20"
    )

    assert response.status_code == 400


def test_geology_summary_endpoint_isolated_from_missing_project(client):
    response = client.get(
        "/api/projects/missing-summary/geology/summary/"
        "?bbox=121,25,121.1,25.1&limit=20"
    )

    assert response.status_code == 404


def test_summary_aggregates_five_layers_and_preserves_layer_statuses(
    client, django_user_model, monkeypatch
):
    user = django_user_model.objects.create_user(username="summary-live", password="secret")
    project = Project.objects.create(slug="summary-live", name="Summary live", owner=user)
    Plan.objects.create(
        project=project,
        name="Formal worksite",
        status="published",
        geometry={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]},
                "properties": {"administrative_area": "測試行政區"},
            }],
        },
    )
    responses = {
        "drill": {"status": "live", "fetched_at": "2026-08-24T00:00:00Z", "source": "GeologyCloud", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.005, 25.005]}, "properties": {"Project_No": "P1", "Hole_Point_No": "H1", "Depth": 30}}], "screening": {"status": "available", "intersects": True, "nearest_distance_m": 0, "nearby_feature_count": 1, "nearest_drill": {"properties": {"Project_No": "P1", "Hole_Point_No": "H1", "Depth": 30, "geology_id": "P1+H1"}}}, "error": None},
        "liquefaction": {"status": "live", "fetched_at": "2026-08-24T00:00:01Z", "source": "GeologyCloud", "features": [], "screening": {"status": "available", "intersects": False, "nearest_distance_m": None, "nearby_feature_count": 0, "nearest_drill": None}, "error": None},
        "dip_slope": {"status": "cached", "fetched_at": "2026-08-23T00:00:00Z", "source": "GeologyCloud", "features": [], "screening": {"status": "available", "intersects": False, "nearest_distance_m": None, "nearby_feature_count": 0, "nearest_drill": None}, "error": None},
        "rock_fall": {"status": "stale", "fetched_at": "2026-08-22T00:00:00Z", "source": "GeologyCloud", "features": [], "screening": {"status": "available", "intersects": False, "nearest_distance_m": None, "nearby_feature_count": 0, "nearest_drill": None}, "error": "upstream unavailable"},
        "debris_flow_track": {"status": "error", "fetched_at": None, "source": "GeologyCloud", "features": [], "screening": {"status": "error", "intersects": None, "nearest_distance_m": None, "nearby_feature_count": 0, "nearest_drill": None}, "error": "timeout"},
    }

    class FakeLayerService:
        def __init__(self, **kwargs):
            pass

        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None):
            return responses[layer]

    monkeypatch.setattr("worksite_gis.services.geology_summary.GeologyCloudService", FakeLayerService)
    client.force_login(user)
    payload = client.get("/api/projects/summary-live/geology/summary/?bbox=121,25,121.1,25.1&limit=20").json()

    assert payload["status"] == "partial"
    assert len(payload["layers"]) == 5
    assert {item["status"] for item in payload["layers"]} == {"live", "cached", "stale", "error"}
    drill = next(item for item in payload["layers"] if item["layer"] == "drill")
    assert drill["screening"]["nearest_drill"]["properties"]["geology_id"] == "P1+H1"
    overview = next(card for card in payload["cards"] if card["id"] == "worksite-geology-overview")
    assert overview["values"]["requested_layers"] == 5
    assert overview["values"]["stale_layers"] == 1
    assert overview["values"]["error_layers"] == 1


def test_summary_marks_missing_formal_geometry_unavailable(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="summary-no-geometry", password="secret")
    Project.objects.create(slug="summary-no-geometry", name="No geometry", owner=user)
    client.force_login(user)
    payload = client.get("/api/projects/summary-no-geometry/geology/summary/?bbox=121,25,121.1,25.1&limit=20").json()

    assert payload["status"] == "unavailable"
    assert any(item["screening"]["status"] == "no_worksite_geometry" for item in payload["layers"])
    assert any(action["id"] == "confirm-worksite-geometry" for action in payload["actions"])


def test_summary_calculates_liquefaction_intersection_area_and_ratio(monkeypatch):
    from worksite_gis.services.geology_summary import GeologySummaryService

    worksite = Polygon([(121, 25), (121.01, 25), (121.01, 25.01), (121, 25.01)])
    feature = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[121.005, 25], [121.015, 25], [121.015, 25.01], [121.005, 25.01], [121.005, 25]]]}, "properties": {}}
    result = GeologySummaryService.liquefaction_metrics({"features": [feature]}, worksite)

    assert result["intersection_area_m2"] > 0
    assert 0 < result["intersection_ratio"] < 1


def test_summary_distinguishes_candidate_and_returned_feature_counts(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="summary-limit", password="secret")
    project = Project.objects.create(slug="summary-limit", name="Summary limit", owner=user)
    Plan.objects.create(project=project, name="Formal worksite", status="published", geometry={"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]}, "properties": {"administrative_area": "測試"}}]})
    feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.005, 25.005]}, "properties": {"Project_No": "P1", "Hole_Point_No": "H1", "Depth": 1}}
    responses = {layer: {"status": "live", "fetched_at": "2026-08-24T00:00:00Z", "features": [feature] * 140, "screening": {"status": "available", "intersects": True, "nearest_distance_m": 0, "nearby_feature_count": 140, "nearest_drill": feature}, "error": None} for layer in ("drill", "liquefaction", "dip_slope", "rock_fall", "debris_flow_track")}

    class FakeLayerService:
        def __init__(self, **kwargs):
            pass
        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None):
            return responses[layer]

    monkeypatch.setattr("worksite_gis.services.geology_summary.GeologyCloudService", FakeLayerService)
    client.force_login(user)
    payload = client.get("/api/projects/summary-limit/geology/summary/?bbox=121,25,121.1,25.1&limit=20").json()
    drill = next(item for item in payload["layers"] if item["layer"] == "drill")
    assert drill["candidate_feature_count"] == 140
    assert drill["returned_feature_count"] == 20
    assert drill["screening"]["nearby_feature_count_scope"] == "candidate_features"


def test_summary_runs_layers_in_parallel_and_isolates_timeout(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="summary-timeout", password="secret")
    project = Project.objects.create(slug="summary-timeout", name="Summary timeout", owner=user)
    Plan.objects.create(project=project, name="Formal worksite", status="published", geometry={"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]}, "properties": {"administrative_area": "測試"}}]})

    class SlowLayerService:
        def __init__(self, **kwargs):
            pass
        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None):
            time.sleep(0.2)
            if layer == "rock_fall":
                raise TimeoutError("layer timeout")
            return {"status": "live", "fetched_at": "2026-08-24T00:00:00Z", "features": [], "screening": {"status": "available", "intersects": False, "nearby_feature_count": 0}, "error": None}

    monkeypatch.setattr("worksite_gis.services.geology_summary.GeologyCloudService", SlowLayerService)
    client.force_login(user)
    started = time.monotonic()
    response = client.get("/api/projects/summary-timeout/geology/summary/?bbox=121,25,121.1,25.1&limit=20")
    elapsed = time.monotonic() - started
    payload = response.json()
    assert response.status_code == 200
    assert elapsed < 0.7
    assert payload["status"] == "partial"
    failed = next(item for item in payload["layers"] if item["layer"] == "rock_fall")
    assert failed["status"] == "error"
    assert any(item["status"] == "live" for item in payload["layers"] if item["layer"] != "rock_fall")


def test_summary_passes_request_thread_cache_context_to_workers(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="summary-cache-context", password="secret")
    project = Project.objects.create(slug="summary-cache-context", name="Summary cache context", owner=user)
    Plan.objects.create(project=project, name="Formal worksite", status="published", geometry={
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]}, "properties": {"administrative_area": "Test area"}}],
    })
    contexts = []

    class ContextAwareLayerService:
        def __init__(self, **kwargs):
            pass

        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None, cache_context=None):
            contexts.append(cache_context)
            return {"status": "live", "fetched_at": "2026-08-24T00:00:00Z", "features": [], "screening": {"status": "available", "intersects": False, "nearby_feature_count": 0}, "error": None}

    monkeypatch.setattr("worksite_gis.services.geology_summary.GeologyCloudService", ContextAwareLayerService)
    client.force_login(user)
    response = client.get("/api/projects/summary-cache-context/geology/summary/?bbox=121,25,121.1,25.1&limit=20")

    assert response.status_code == 200
    assert len(contexts) == 5
    assert all(isinstance(context, dict) and "cache_key" in context for context in contexts)


def test_summary_preserves_stale_from_request_thread_context(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="summary-stale-context", password="secret")
    project = Project.objects.create(slug="summary-stale-context", name="Summary stale context", owner=user)
    Plan.objects.create(project=project, name="Formal worksite", status="published", geometry={
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]}, "properties": {"administrative_area": "Test area"}}],
    })

    class StaleAwareLayerService:
        def __init__(self, **kwargs):
            pass

        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None, cache_context=None):
            if layer == "drill" and cache_context.get("stale"):
                return {"status": "stale", "fetched_at": "2026-08-20T00:00:00Z", "features": [], "screening": {"status": "available", "intersects": False, "nearby_feature_count": 0}, "error": "TimeoutError: upstream timeout"}
            return {"status": "live", "fetched_at": "2026-08-24T00:00:00Z", "features": [], "screening": {"status": "available", "intersects": False, "nearby_feature_count": 0}, "error": None}

    monkeypatch.setattr("worksite_gis.services.geology_summary.GeologyCloudService", StaleAwareLayerService)
    monkeypatch.setattr(
        "worksite_gis.services.geology_summary.GeologySummaryService._prepare_cache_context",
        lambda self, layer, **kwargs: {"cache_key": f"test:{layer}", "fresh": None, "stale": {"fetched_at": "2026-08-20T00:00:00Z", "payload": {"type": "FeatureCollection", "features": []}} if layer == "drill" else None},
    )
    client.force_login(user)
    payload = client.get("/api/projects/summary-stale-context/geology/summary/?bbox=121,25,121.1,25.1&limit=20").json()

    drill = next(item for item in payload["layers"] if item["layer"] == "drill")
    assert payload["status"] == "partial"
    assert drill["status"] == "stale"


def test_summary_build_does_not_leave_executor_threads_after_deadline(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="summary-thread-cleanup", password="secret")
    project = Project.objects.create(slug="summary-thread-cleanup", name="Summary thread cleanup", owner=user)
    Plan.objects.create(project=project, name="Formal worksite", status="published", geometry={
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]]}, "properties": {"administrative_area": "Test area"}}],
    })

    class SlowLayerService:
        def __init__(self, **kwargs):
            pass

        def fetch_layer(self, layer, *, bbox=None, project=None, limit=None, cache_context=None):
            time.sleep(0.3)
            return {"status": "live", "fetched_at": "2026-08-24T00:00:00Z", "features": [], "screening": {"status": "available", "intersects": False, "nearby_feature_count": 0}, "error": None}

    monkeypatch.setattr("worksite_gis.services.geology_summary.GeologyCloudService", SlowLayerService)
    monkeypatch.setattr("worksite_gis.services.geology_summary.SUMMARY_TOTAL_DEADLINE_SECONDS", 0.01)
    from worksite_gis.services.geology_summary import GeologySummaryService
    before = {thread.name for thread in threading.enumerate() if "ThreadPoolExecutor" in thread.name}
    client.force_login(user)
    response = client.get("/api/projects/summary-thread-cleanup/geology/summary/?bbox=121,25,121.1,25.1&limit=20")
    time.sleep(0.05)
    after = {thread.name for thread in threading.enumerate() if "ThreadPoolExecutor" in thread.name}

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert after == before
