from datetime import timedelta

import pytest
from django.utils import timezone

from worksite_gis.integrations.base import AdapterResult
from worksite_gis.models import ApiSnapshot, Project


class FakeTdxClient:
    calls = []
    responses = {}
    failures = set()

    def __init__(self, *args, **kwargs):
        pass

    def _fetch(self, name):
        self.__class__.calls.append(name)
        if name in self.__class__.failures:
            raise RuntimeError(f"{name} unavailable")
        result = self.__class__.responses.get(name)
        if callable(result):
            return result()
        if result is None:
            raise AssertionError(f"unexpected TDX call: {name}")
        return result

    def fetch_highway_section_shapes(self):
        return self._fetch("highway_shapes")

    def fetch_freeway_section_shapes(self):
        return self._fetch("freeway_shapes")

    def fetch_highway_live(self):
        return self._fetch("highway_live")

    def fetch_freeway_live(self):
        return self._fetch("freeway_live")

    def fetch_highway_sections(self):
        return self._fetch("highway_sections")

    def fetch_freeway_sections(self):
        return self._fetch("freeway_sections")

    def fetch_highway_facility_tracks(self, road_class=3):
        return self._fetch(f"facility_{road_class}")


@pytest.fixture
def traffic_project():
    return Project.objects.create(
        slug="traffic-contract",
        name="Traffic contract",
        search_center_latitude=25.0,
        search_center_longitude=121.5,
        search_radius_km=10,
    )


@pytest.fixture
def fake_tdx(monkeypatch):
    FakeTdxClient.calls = []
    FakeTdxClient.responses = {}
    FakeTdxClient.failures = set()
    monkeypatch.setattr("worksite_gis.views.TdxClient", FakeTdxClient)
    return FakeTdxClient


def _snapshot(kind, payload, *, expired=False, fetched_at=None):
    fetched_at = fetched_at or timezone.now()
    return ApiSnapshot.objects.create(
        service="tdx",
        kind=kind,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(minutes=-1 if expired else 5),
        payload=payload,
        status="ok",
    )


def _traffic_snapshots(*, expired=False, include_outside=False):
    shapes = [
        {"section_id": "H1", "geometry": "LINESTRING(121.49 24.99,121.51 25.01)"},
        {"section_id": "H2", "geometry": "LINESTRING(121.48 24.98,121.49 24.99)"},
    ]
    if include_outside:
        shapes.append({"section_id": "OUT", "geometry": "LINESTRING(122.5 25.0,122.6 25.0)"})
    _snapshot("highway_shapes", shapes, expired=expired)
    _snapshot(
        "highway_live",
        [
            {"section_id": "H1", "travel_time": 60, "travel_speed": 45.5, "congestion_level_id": 2, "congestion_level": "2", "data_collect_time": "2026-08-19T08:00:00+08:00"},
            {"section_id": "H2", "travel_time": 80, "travel_speed": 30, "congestion_level_id": 3, "congestion_level": "3", "data_collect_time": "2026-08-19T08:00:00+08:00"},
        ],
        expired=expired,
    )
    _snapshot(
        "highway_sections",
        [
            {"section_id": "H1", "road_name": "台3線", "direction": "E", "section_name": "A-B"},
            {"section_id": "H2", "road_name": "台13甲", "direction": "W", "section_name": "B-C"},
        ],
        expired=expired,
    )
    _snapshot("freeway_shapes", [{"section_id": "F1", "geometry": "LINESTRING(121.49 25.0,121.51 25.0)"}], expired=expired)
    _snapshot("freeway_live", [{"section_id": "F1", "travel_time": 20, "travel_speed": 88, "congestion_level_id": 1, "congestion_level": "1", "data_collect_time": "2026-08-19T08:00:00+08:00"}], expired=expired)
    _snapshot("freeway_sections", [{"section_id": "F1", "road_name": "國道1號", "direction": "N", "section_name": "交流道A-B"}], expired=expired)


@pytest.mark.django_db
def test_highway_traffic_feature_collection_contract_and_cache_hit(
    client, traffic_project, fake_tdx
):
    _traffic_snapshots()

    response = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/?limit=300")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"type", "status", "stale", "fetched_at", "radius_km", "error", "features", "runtime_state"}
    assert payload["type"] == "FeatureCollection"
    assert payload["status"] == "ok"
    assert payload["stale"] is False
    assert payload["radius_km"] == 10
    assert payload["error"] == ""
    assert fake_tdx.calls == []

    highway = next(item for item in payload["features"] if item["properties"]["section_id"] == "H1")
    freeway = next(item for item in payload["features"] if item["properties"]["section_id"] == "F1")
    assert highway["geometry"]["type"] == "LineString"
    assert highway["properties"] == {
        "travel_time": 60,
        "travel_speed": 45.5,
        "congestion_level_id": 2,
        "congestion_level": "2",
        "data_collect_time": "2026-08-19T08:00:00+08:00",
        "section_id": "H1",
        "road_type": "highway",
        "road_name": "台3線",
        "direction": "E",
        "section_name": "A-B",
    }
    assert freeway["properties"]["road_type"] == "freeway"
    assert freeway["properties"]["road_name"] == "國道1號"


@pytest.mark.django_db
def test_highway_traffic_applies_radius_limit_and_skips_invalid_geometry(
    client, traffic_project, fake_tdx
):
    _traffic_snapshots(include_outside=True)

    response = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/?limit=1")

    assert response.status_code == 200
    features = response.json()["features"]
    assert len(features) == 1
    assert features[0]["properties"]["section_id"] == "H1"
    assert all(item["properties"]["section_id"] not in {"BAD", "OUT"} for item in features)


@pytest.mark.django_db
def test_highway_traffic_invalid_wkt_is_skipped_without_fake_geometry(
    client, traffic_project, fake_tdx
):
    _snapshot("highway_shapes", [{"section_id": "GOOD", "geometry": "LINESTRING(121.49 24.99,121.51 25.01)"}, {"section_id": "BAD", "geometry": "not-wkt"}])
    _snapshot("highway_live", [{"section_id": "GOOD", "travel_speed": 45}, {"section_id": "BAD", "travel_speed": 0}])
    _snapshot("highway_sections", [{"section_id": "GOOD", "road_name": "Good", "direction": "E", "section_name": "A-B"}, {"section_id": "BAD", "road_name": "Bad", "direction": "", "section_name": ""}])
    _snapshot("freeway_shapes", [])
    _snapshot("freeway_live", [])
    _snapshot("freeway_sections", [])

    response = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert [item["properties"]["section_id"] for item in payload["features"]] == ["GOOD"]
    assert payload["features"][0]["properties"]["travel_speed"] == 45

@pytest.mark.django_db
def test_highway_traffic_stale_fallback_preserves_geojson_and_error(
    client, traffic_project, fake_tdx
):
    _traffic_snapshots(expired=True)
    fake_tdx.failures = {"highway_shapes", "highway_live", "highway_sections", "freeway_shapes", "freeway_live", "freeway_sections"}

    response = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["stale"] is True
    assert "unavailable" in payload["error"]
    feature = next(item for item in payload["features"] if item["properties"]["section_id"] == "H1")
    assert feature["geometry"]["type"] == "LineString"
    assert feature["properties"]["travel_speed"] == 45.5


@pytest.mark.django_db
def test_highway_traffic_without_cache_returns_degraded_empty_collection(
    client, traffic_project, fake_tdx
):
    fake_tdx.failures = {"highway_shapes", "highway_live", "highway_sections", "freeway_shapes", "freeway_live", "freeway_sections"}

    response = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["stale"] is False
    assert payload["features"] == []
    assert payload["error"]


@pytest.mark.django_db
def test_facility_tracks_contract_filters_and_uses_separate_cache_kind(
    client, fake_tdx
):
    _snapshot(
        "highway_facility_3",
        [
            {"road_name": "台3線", "road_class": 3, "cities": [{"name": "新竹縣", "code": "HsinchuCounty"}]},
            {"road_name": "台13甲", "road_class": 3, "cities": [{"name": "苗栗縣", "code": "MiaoliCounty"}]},
        ],
    )
    _snapshot("highway_facility_4", [{"road_name": "國道1號", "road_class": 4, "cities": [{"name": "桃園市", "code": "TaoyuanCity"}]}])

    response = client.get("/api/highway-facility-tracks/?road_class=3&q=13&city=MiaoliCounty&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"status", "road_class", "stale", "fetched_at", "error", "records", "runtime_state"}
    assert payload["status"] == "ok"
    assert payload["road_class"] == 3
    assert payload["stale"] is False
    assert payload["error"] == ""
    assert payload["records"] == [{
        "road_name": "台13甲",
        "road_class": 3,
        "cities": [{"name": "苗栗縣", "code": "MiaoliCounty"}],
    }]
    assert fake_tdx.calls == []
    assert "features" not in payload


@pytest.mark.django_db
def test_facility_cache_kind_isolated_by_road_class(client, fake_tdx):
    _snapshot("highway_facility_3", [{"road_name": "台3線", "road_class": 3, "cities": []}])
    _snapshot("highway_facility_4", [{"road_name": "國道1號", "road_class": 4, "cities": []}])

    class_id_3 = client.get("/api/highway-facility-tracks/?road_class=3")
    class_id_4 = client.get("/api/highway-facility-tracks/?road_class=4")

    assert class_id_3.json()["records"][0]["road_name"] == "台3線"
    assert class_id_4.json()["records"][0]["road_name"] == "國道1號"
    assert fake_tdx.calls == []


@pytest.mark.django_db
def test_facility_stale_fallback_is_degraded_and_keeps_records(client, fake_tdx):
    _snapshot(
        "highway_facility_3",
        [{"road_name": "台3線", "road_class": 3, "cities": [{"name": "新竹縣", "code": "HsinchuCounty"}]}],
        expired=True,
    )
    fake_tdx.failures = {"facility_3"}

    response = client.get("/api/highway-facility-tracks/?road_class=3")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["stale"] is True
    assert payload["records"][0]["road_name"] == "台3線"
    assert payload["error"] == "upstream_unavailable"


@pytest.mark.django_db
def test_traffic_project_and_parameter_errors_keep_existing_statuses(client, traffic_project, fake_tdx):
    traffic_project.search_center_latitude = None
    traffic_project.search_center_longitude = None
    traffic_project.save(update_fields=["search_center_latitude", "search_center_longitude"])

    assert client.get("/api/projects/missing/highway-traffic/").status_code == 404
    assert client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/").status_code == 409

    located_project = Project.objects.create(
        slug="traffic-parameter-contract",
        name="Traffic parameter contract",
        search_center_latitude=25.0,
        search_center_longitude=121.5,
        search_radius_km=10,
    )
    assert client.get(f"/api/projects/{located_project.slug}/highway-traffic/?limit=nope").status_code == 400
    assert client.get("/api/highway-facility-tracks/?road_class=nope").status_code == 400


@pytest.mark.django_db
def test_traffic_limit_and_road_class_are_bounded(client, traffic_project, fake_tdx):
    _traffic_snapshots()
    _snapshot("highway_facility_3", [{"road_name": "台3線", "road_class": 3, "cities": []}])

    negative_limit = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/?limit=-1")
    oversized_limit = client.get(f"/api/highway-facility-tracks/?limit=999&road_class=99")

    assert negative_limit.status_code == 200
    assert len(negative_limit.json()["features"]) <= 1
    assert oversized_limit.status_code == 200
    assert oversized_limit.json()["road_class"] == 9
    assert oversized_limit.json()["records"] == []


@pytest.mark.django_db
def test_traffic_adapter_error_result_is_degraded_without_real_network(
    client, traffic_project, fake_tdx
):
    fake_tdx.responses["highway_live"] = AdapterResult(
        "error", "tdx", timezone.now(), [], False, "TDX credential missing"
    )
    fake_tdx.failures = {"highway_shapes", "highway_sections", "freeway_shapes", "freeway_live", "freeway_sections"}

    response = client.get(f"/api/projects/{traffic_project.slug}/highway-traffic/")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["error"] == "upstream_unavailable"
