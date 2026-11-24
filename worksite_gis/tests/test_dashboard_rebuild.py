import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import pytest
from django.core.management import call_command
from openpyxl import Workbook

from worksite_gis.integrations.base import AdapterResult
from worksite_gis.models import Alert, Plan, Project, StationSeed
from worksite_gis.planning import validate_feature_collection


@pytest.mark.django_db
def test_empty_project_list_creates_exact_worksite_seed(client, django_user_model):
    user = django_user_model.objects.create_user(username="project-seed-reader")
    client.force_login(user)
    response = client.get("/api/projects/")
    project = response.json()["projects"][0]
    assert project["name"] == "Worksite GIS — Engineering Geology Screening"
    assert project["center"] == {"latitude": 24.985556, "longitude": 121.310278}
    assert project["timezone"] == "Asia/Taipei"
    assert project["measurement_crs"] == "EPSG:3826"


@pytest.mark.django_db
def test_location_search_returns_candidate_then_saves_fixed_radius(client, django_user_model):
    user = django_user_model.objects.create_user(username="locator", password="pw")
    Project.objects.create(slug="worksite", name="WORKSITE", owner=user)
    client.force_login(user)
    searched = client.post("/api/projects/worksite/location-search/", data={"query": "24.985556,121.310278"}, content_type="application/json")
    assert searched.status_code == 200
    candidate = searched.json()["candidates"][0]
    assert candidate["provider"] == "coordinates"
    saved = client.post("/api/projects/worksite/search-center/", data={**candidate, "radius_km": 25}, content_type="application/json")
    assert saved.status_code == 200
    assert saved.json()["search_center"]["radius_km"] == 10
    project = Project.objects.get(slug="worksite")
    assert project.search_center_label == candidate["label"]
    assert project.setup_status == "located"


@pytest.mark.django_db
def test_project_cctv_requires_center_and_filters_to_two_km_with_display_fields(client, monkeypatch):
    Project.objects.create(slug="unset", name="Unset")
    assert client.get("/api/projects/unset/nearby/cctv/").status_code == 409
    Project.objects.create(slug="set", name="Set", search_center_latitude=24.985556, search_center_longitude=121.310278)

    class Cache:
        def __init__(self, *args, **kwargs): pass
        def fetch(self, adapter):
            return AdapterResult("ok", "TDX", datetime.now(dt_timezone.utc), [
                {"cctv_id": "near", "name": "Near", "city": "南投縣", "latitude": 24.99, "longitude": 121.31, "stream_url": "https://example.test/near.jpg", "source": "TDX", "status": "live"},
                {"cctv_id": "far", "name": "Far", "city": "南投縣", "latitude": 25.5, "longitude": 121.31, "stream_url": "https://example.test/far.jpg", "source": "TDX", "status": "live"},
            ])

    monkeypatch.setattr("worksite_gis.views.SnapshotCache", Cache)
    response = client.get("/api/projects/set/nearby/cctv/?limit=50")
    assert response.status_code == 200
    assert [item["cctv_id"] for item in response.json()["records"]] == ["near"]
    assert response.json()["radius_km"] == 2
    assert response.json()["records"] == [{
        "cctv_id": "near", "name": "Near", "city": "南投縣", "latitude": 24.99, "longitude": 121.31,
        "stream_url": "https://example.test/near.jpg", "source": "TDX", "status": "live",
        "distance_km": 0.495,
        "connection_status": "unverified",
    }]


@pytest.mark.django_db
def test_project_cctv_local_camera_exposes_connection_status_and_city(client, monkeypatch):
    from worksite_gis.models import CctvCamera

    Project.objects.create(slug="local", name="Local", search_center_latitude=24.985556, search_center_longitude=121.310278)
    CctvCamera.objects.create(provider="Nantou", camera_id="cam-1", city="南投縣", road_name="道路 151", location_name="南投縣道路 151", latitude=24.99, longitude=121.31, stream_url="https://example.test/cam.jpg", media_type="image")
    CctvCamera.objects.create(provider="Nantou", camera_id="cam-2", city="南投縣", road_name="道路 152", location_name="南投縣道路 152", latitude=24.991, longitude=121.31, stream_url="", media_type="")

    class Cache:
        def __init__(self, *args, **kwargs): pass
        def fetch(self, adapter): return AdapterResult("error", "tdx", datetime.now(dt_timezone.utc), [], False, "offline")

    monkeypatch.setattr("worksite_gis.views.SnapshotCache", Cache)
    response = client.get("/api/projects/local/nearby/cctv/")
    records = {item["cctv_id"]: item for item in response.json()["records"]}

    assert records["cam-1"]["city"] == "南投縣"
    assert records["cam-1"]["connection_status"] == "unverified"
    assert records["cam-2"]["connection_status"] == "offline"


@pytest.mark.django_db
def test_project_weather_stations_only_returns_cwa_data_in_saved_center_radius(client, monkeypatch):
    from datetime import datetime, timezone as dt_timezone
    from worksite_gis.integrations.base import AdapterResult

    Project.objects.create(slug="site", name="Site", search_center_latitude=24.98, search_center_longitude=121.31)

    class Cache:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, adapter):
            return AdapterResult("ok", "cwa", datetime.now(dt_timezone.utc), [
                {"station_id": "near", "name": "Near", "latitude": 24.99, "longitude": 121.31,
                 "source": "CWA", "status": "observed"},
                {"station_id": "far", "name": "Far", "latitude": 25.5, "longitude": 121.31,
                 "source": "CWA", "status": "observed"},
            ])

    monkeypatch.setattr("worksite_gis.views.SnapshotCache", Cache)
    response = client.get("/api/projects/site/nearby/weather-stations/")
    assert [item["external_id"] for item in response.json()["records"]] == ["near"]


@pytest.mark.django_db
def test_project_weather_stations_uses_cwa_observations_and_filters_project_radius(client, monkeypatch):
    from datetime import datetime, timezone as dt_timezone
    from worksite_gis.integrations.base import AdapterResult

    Project.objects.create(slug="cwa-site", name="CWA site", search_center_latitude=24.98, search_center_longitude=121.31, search_radius_km=10)

    class Cache:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, adapter):
            return AdapterResult("ok", "cwa", datetime.now(dt_timezone.utc), [
                {"station_id": "near-cwa", "name": "Near CWA", "latitude": 24.99, "longitude": 121.31,
                 "observed_at": "2026-08-13T10:00:00+08:00", "temperature": 31.2, "humidity": 65,
                 "wind_speed": 2.3, "precipitation": 0.5, "source": "CWA", "status": "observed"},
                {"station_id": "far-cwa", "name": "Far CWA", "latitude": 25.5, "longitude": 121.31,
                 "observed_at": "2026-08-13T10:00:00+08:00", "temperature": 30.0, "humidity": 70,
                 "wind_speed": 1.2, "precipitation": 0, "source": "CWA", "status": "observed"},
            ])

    monkeypatch.setattr("worksite_gis.views.SnapshotCache", Cache)
    response = client.get("/api/projects/cwa-site/nearby/weather-stations/")

    assert response.status_code == 200
    assert response.json()["source"] == "cwa"
    assert response.json()["records"] == [{
        "external_id": "near-cwa", "name": "Near CWA", "latitude": 24.99, "longitude": 121.31,
        "distance_km": 1.112, "observed_at": "2026-08-13T10:00:00+08:00", "temperature": 31.2,
        "humidity": 65, "wind_speed": 2.3, "precipitation": 0.5, "source": "CWA", "status": "observed",
    }]


@pytest.mark.django_db
def test_project_weather_stations_returns_service_unavailable_when_cwa_fails(client, monkeypatch):
    from datetime import datetime, timezone as dt_timezone
    from worksite_gis.integrations.base import AdapterResult

    Project.objects.create(slug="offline-site", name="Offline site", search_center_latitude=24.98, search_center_longitude=121.31)

    class Cache:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, adapter):
            return AdapterResult("error", "cwa", datetime.now(dt_timezone.utc), [], False, "connection refused")

    monkeypatch.setattr("worksite_gis.views.SnapshotCache", Cache)
    response = client.get("/api/projects/offline-site/nearby/weather-stations/")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


@pytest.mark.django_db
def test_cctv_xlsx_import_is_idempotent_and_reports_invalid_rows():
    from worksite_gis.models import CctvCamera
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["編號", "道路名稱", "位置名稱", "經度", "緯度", "即時影像網址"])
    sheet.append(["cam-1", "介壽路", "建國路口", 121.310278, 24.985556, "https://example.test/cam.jpg"])
    sheet.append(["bad", "道路", "錯誤座標", 999, 999, ""])
    source = Path(".test-artifacts") / "cctv-import" / "cctv.xlsx"
    source.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(source)
    call_command("import_cctv_xlsx", file=str(source), source="test_snapshot")
    call_command("import_cctv_xlsx", file=str(source), source="test_snapshot")
    assert CctvCamera.objects.count() == 1
    assert CctvCamera.objects.get().camera_id == "cam-1"


@pytest.mark.django_db
def test_plan_statuses_and_version_history_endpoint(client, django_user_model):
    user = django_user_model.objects.create_user(username="reviewer", password="pw", is_staff=True)
    client.force_login(user)
    Project.objects.create(slug="worksite", name="WORKSITE", measurement_crs="EPSG:3826")
    geometry = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"category": "正式工程範圍", "source": "survey"}, "geometry": {"type": "Polygon", "coordinates": [[[121.30,24.98],[121.31,24.98],[121.31,24.99],[121.30,24.99],[121.30,24.98]]]}}]}
    created = client.post("/api/projects/worksite/plans/", data={"name": "正式工區", "status": "draft", "geometry": geometry, "change_summary": "建立草稿"}, content_type="application/json")
    assert created.status_code == 201
    plan_id = created.json()["plan"]["id"]
    base = f"/api/projects/worksite/plans/{plan_id}/"
    assert client.post(base + "submit/", headers={"X-WORKSITE-Lock-Version": "1"}).status_code == 200
    published = client.post(base + "review/", {"decision": "publish"}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": "2"})
    assert published.status_code == 200
    history = client.get(base + "versions/")
    assert history.status_code == 200
    assert history.json()["versions"][0]["status"] == "draft"
    assert history.json()["events"][-1]["action"] == "published"


@pytest.mark.django_db
def test_dashboard_exposes_four_regions_and_worksite_flow(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="dashboard-layout", password="Valid-pass-123"))
    page = client.get("/map/").content.decode("utf-8")
    for element_id in ("battle-header", "layer-drawer", "map", "intel-panel", "project-switcher", "location-search-form", "start-worksite-edit", "settings-modal", "alert-overlay"):
        assert f'id="{element_id}"' in page
    assert "Worksite GIS — Engineering Geology Screening" in page
    assert "工程地質檢核" in page


@pytest.mark.django_db
def test_alert_history_is_retained_at_fifty_records():
    from django.utils import timezone
    for index in range(55):
        Alert.record(source="test", severity="yellow", title=f"Alert {index}", occurred_at=timezone.now())
    assert Alert.objects.count() == 50


def test_project_without_local_crs_uses_geodesic_measurement():
    geometry = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}}]}
    result = validate_feature_collection(geometry, measurement_crs="")
    assert result["measurement_method"] == "geodesic"
    assert 111000 < result["geometry"]["features"][0]["properties"]["length_m"] < 112000
