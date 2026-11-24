import pytest
import json

from worksite_gis.models import Alert, ApiServiceConfig, ApiSnapshot, LayerCatalog, Plan, Project, StationSeed


def _authenticated_map_page(client, django_user_model, username):
    user = django_user_model.objects.create_user(username=username, password="Valid-pass-123")
    client.force_login(user)
    return client.get("/map/")


@pytest.mark.django_db
def test_layers_endpoint_returns_transport_layer(client):
    LayerCatalog.objects.create(slug="mrt_stations", name="捷運車站", category="transport", path="data/processed/gis/transport/mrt_stations.geojson")
    response = client.get("/api/layers/")
    assert response.status_code == 200
    assert any(item["slug"] == "mrt_stations" for item in response.json()["layers"])


@pytest.fixture
def configured_tdx(settings, monkeypatch):
    from worksite_gis.integrations.tdx_transport import TdxError

    settings.TDX_CLIENT_ID = "synthetic-client"
    settings.TDX_CLIENT_SECRET = "synthetic-secret"

    def unavailable(*args, **kwargs):
        raise TdxError("upstream_unavailable")

    monkeypatch.setattr("worksite_gis.integrations.tdx_transport.TdxTransport.get", unavailable)


@pytest.mark.django_db
def test_highway_traffic_endpoint_joins_cached_shapes_and_live_status(client, configured_tdx):
    from datetime import timedelta
    from django.utils import timezone
    now = timezone.now()
    project = Project.objects.create(slug="traffic", name="Traffic", search_center_latitude=25.0, search_center_longitude=121.5, search_radius_km=10)
    ApiSnapshot.objects.create(service="tdx", kind="highway_shapes", fetched_at=now, expires_at=now + timedelta(minutes=5), payload=[{"section_id": "S1", "geometry": "LINESTRING(121.49 24.99,121.51 25.01)"}])
    ApiSnapshot.objects.create(service="tdx", kind="highway_live", fetched_at=now, expires_at=now + timedelta(minutes=5), payload=[{"section_id": "S1", "travel_speed": 22.5, "congestion_level": "3", "data_collect_time": "2026-08-17T10:18:34+08:00"}])
    ApiSnapshot.objects.create(service="tdx", kind="highway_sections", fetched_at=now, expires_at=now + timedelta(days=1), payload=[{"section_id": "S1", "road_name": "台13甲", "direction": "E", "section_name": "台13甲_0K+0~1K+0"}])
    response = client.get(f"/api/projects/{project.slug}/highway-traffic/?limit=300")
    assert response.status_code == 200
    assert response.json()["features"][0]["properties"]["section_id"] == "S1"
    assert response.json()["features"][0]["properties"]["travel_speed"] == 22.5
    assert response.json()["features"][0]["properties"]["road_name"] == "台13甲"


@pytest.mark.django_db
def test_highway_facility_tracks_endpoint_filters_cached_roads(client, configured_tdx):
    from datetime import timedelta
    from django.utils import timezone
    now = timezone.now()
    ApiSnapshot.objects.create(service="tdx", kind="highway_facility_3", fetched_at=now, expires_at=now + timedelta(days=1), payload=[
        {"road_name": "台13甲", "road_class": 3, "cities": [{"name": "苗栗縣", "code": "MiaoliCounty"}]},
        {"road_name": "台3", "road_class": 3, "cities": [{"name": "新竹縣", "code": "HsinchuCounty"}]},
    ])
    response = client.get("/api/highway-facility-tracks/?road_class=3&q=13")
    assert response.status_code == 200
    assert [item["road_name"] for item in response.json()["records"]] == ["台13甲"]


@pytest.mark.django_db
def test_layers_endpoint_returns_public_catalog_metadata(client):
    LayerCatalog.objects.create(slug="catalog_meta", name="Catalog", category="transport", path="data/processed/gis/transport/mrt_stations.geojson", source="source.geojson", crs="EPSG:4326", feature_count=12, metadata={"validation": {"ok": True}})
    response = client.get("/api/layers/")
    assert response.status_code == 200
    item = next(item for item in response.json()["layers"] if item["slug"] == "catalog_meta")
    assert item["crs"] == "EPSG:4326"
    assert item["feature_count"] == 12
    assert "source" not in item
    assert "path" not in item
    assert "metadata" not in item


@pytest.mark.django_db
def test_public_layers_hide_internal_paths_and_table_outputs(client):
    LayerCatalog.objects.create(
        slug="public_hazard",
        name="Public hazard",
        category="hazards",
        format="geojson",
        path="data/processed/gis/hazards/debris_flow.geojson",
        source="GIS圖資/secret-source.shp",
        metadata={"source_hash": "private"},
    )
    LayerCatalog.objects.create(
        slug="private_cctv_table",
        name="Private CCTV table",
        category="api_seed",
        format="json",
        path="data/processed/api_seed/cctv_stations.json",
        source="api資料表/CCTV/source.xlsx",
    )

    response = client.get("/api/layers/")

    assert response.status_code == 200
    assert response.json()["layers"] == [{
        "slug": "public_hazard",
        "name": "Public hazard",
        "category": "hazards",
        "format": "geojson",
        "crs": "",
        "feature_count": 0,
        "delivery": "full",
        "updated_at": response.json()["layers"][0]["updated_at"],
    }]
    assert client.get("/api/layers/private_cctv_table/").status_code == 404


@pytest.mark.django_db
def test_spatial_layer_detail_requires_bbox_and_returns_only_viewport_features(client):
    from pathlib import Path
    from django.test import override_settings
    from worksite_gis.data_pipeline.spatial_index import build_spatial_index

    root = Path(".test-artifacts") / "spatial-layer-api"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "flood.geojson"
    index = root / "risk.sqlite3"
    source.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"name":"inside"},"geometry":{"type":"Polygon","coordinates":[[[120,23],[121,23],[121,24],[120,24],[120,23]]]}},'
        '{"type":"Feature","properties":{"name":"outside"},"geometry":{"type":"Polygon","coordinates":[[[122,25],[123,25],[123,26],[122,26],[122,25]]]}}]}',
        encoding="utf-8",
    )
    build_spatial_index([("flood", "hazards", source)], index)
    LayerCatalog.objects.create(
        slug="flood",
        name="Flood",
        category="hazards",
        format="spatial",
        path=str(source),
        metadata={"spatial": {"indexed": True, "index_path": str(index)}},
    )

    with override_settings(SPATIAL_INDEX_PATH=index):
        listing = client.get("/api/layers/")
        missing_bbox = client.get("/api/layers/flood/")
        response = client.get("/api/layers/flood/?bbox=120.25,23.25,120.75,23.75&limit=20")

    assert missing_bbox.status_code == 400
    assert listing.json()["layers"][0]["format"] == "spatial"
    assert listing.json()["layers"][0]["delivery"] == "bbox"
    assert response.status_code == 200
    assert response.json()["type"] == "FeatureCollection"
    assert [item["properties"]["name"] for item in response.json()["features"]] == ["inside"]


@pytest.mark.django_db
def test_spatial_sqlite_catalog_is_delivered_by_bbox_without_metadata_flag(client):
    from pathlib import Path
    from django.test import override_settings
    from worksite_gis.data_pipeline.spatial_index import build_spatial_index

    root = Path(".test-artifacts") / "spatial-layer-inference"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "flood.geojson"
    index = root / "risk_index.sqlite3"
    source.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"name":"inside"},"geometry":{"type":"Polygon","coordinates":[[[120,23],[121,23],[121,24],[120,24],[120,23]]]}}]}',
        encoding="utf-8",
    )
    build_spatial_index([("flood_650mm_24h", "hazards", source)], index)
    LayerCatalog.objects.create(slug="flood_650mm_24h", name="Flood", category="hazards", format="spatial", path=str(index))

    with override_settings(SPATIAL_INDEX_PATH=index):
        listing = client.get("/api/layers/")
        response = client.get("/api/layers/flood_650mm_24h/?bbox=120.1,23.1,120.9,23.9&limit=20")

    assert listing.json()["layers"][0]["delivery"] == "bbox"
    assert response.status_code == 200
    assert response.json()["features"][0]["properties"]["name"] == "inside"


@pytest.mark.django_db
def test_risk_endpoint_reports_intersecting_hazard(client):
    from pathlib import Path
    from django.test import override_settings
    from worksite_gis.data_pipeline.spatial_index import build_spatial_index

    layer_path = Path(".test-artifacts") / "hazard.geojson"
    layer_path.parent.mkdir(exist_ok=True)
    layer_path.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"level":"high"},"geometry":{"type":"Polygon","coordinates":[[[120,23],[121,23],[121,24],[120,24],[120,23]]]}}]}',
        encoding="utf-8",
    )
    LayerCatalog.objects.create(slug="test_hazard", name="Test hazard", category="hazards", path=str(layer_path))
    index_path = Path(".test-artifacts") / "hazard-risk.sqlite3"
    build_spatial_index([("test_hazard", "hazards", layer_path)], index_path)
    layer_path.unlink()
    with override_settings(SPATIAL_INDEX_PATH=index_path):
        response = client.get("/api/risk/?lat=23.5&lng=120.5")
    assert response.status_code == 200
    assert response.json()["layers"][0]["slug"] == "test_hazard"


@pytest.mark.django_db
def test_risk_endpoint_reports_missing_spatial_index(client):
    from pathlib import Path
    from django.test import override_settings

    missing = Path(".test-artifacts") / "missing-risk-index.sqlite3"
    missing.unlink(missing_ok=True)
    with override_settings(SPATIAL_INDEX_PATH=missing):
        response = client.get("/api/risk/?lat=23.5&lng=120.5")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "spatial index unavailable"


def test_map_page_is_available(client, django_user_model):
    response = _authenticated_map_page(client, django_user_model, "map-page")
    assert response.status_code == 200
    page = response.content.decode("utf-8")
    assert "即時資料" in page
    assert "路況壅塞水準" in page
    page = response.content.decode()
    assert "layer-drawer" in page
    assert "plan-list" in page
    assert 'id="project-switcher"' in page
    assert "保存搜尋中心" in response.content.decode()


def test_esri_satellite_basemap_is_available():
    from pathlib import Path

    template = Path("home/templates/home/map.html").read_text(encoding="utf-8")
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert 'data-basemap="esri-satellite"' in template
    assert "World_Imagery/MapServer/tile/{z}/{y}/{x}" in script
    assert "Esri World Imagery" in template
    assert "Esri Satellite Imagery" in script


def test_transport_layer_styles_use_rail_and_mrt_line_colors():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    css = Path("home/static/home/css/mrt.css").read_text(encoding="utf-8")
    assert "#2182CC" in script
    assert "mrtLineColor" in script
    assert "淡水信義線" in script
    assert "板南線" in script


def test_mrt_palette_matches_operator_specific_colors():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    for color in ("#9E652E", "#E3002C", "#008559", "#FFA300", "#0070BD", "#FFD300", "#3A0078", "#84BD00", "#8B4513", "#E3002D", "#F6821F", "#72B332"):
        assert color in script
    assert "臺北捷運" in script and "桃園捷運" in script and "高雄捷運" in script


def test_mrt_network_combines_routes_and_stations_with_scalable_hex_icons():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    css = Path("home/static/home/css/mrt.css").read_text(encoding="utf-8")
    assert "捷運路線／車站" in script
    assert "loadMrtNetwork" in script
    assert "updateMrtStationScale" in script
    assert "clip-path: polygon" in css
    assert ".mrt-transfer-marker span { clip-path: none; border-radius: 0; }" in css
    assert "color: #000" in css


def test_mrt_station_icon_scales_inner_hexagon_with_map_zoom():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    css = Path("home/static/home/css/mrt.css").read_text(encoding="utf-8")
    assert "width:' + (width / entries.length) + 'px;height:' + height + 'px" in script
    assert "Math.max(.15, Math.min(1.6" in script
    assert "background: #fff" in css
    assert "border: 3px solid var(--station-color" in css
    assert "resolveMrtStationCollisions" in script
    assert "latLngToContainerPoint" in script
    assert "groupMrtStationFeatures" in script
    assert "mrt-transfer-marker" in script


def test_google_basemap_code_and_configuration_are_removed():
    from pathlib import Path

    template = Path("home/templates/home/map.html").read_text(encoding="utf-8")
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert 'data-basemap="google"' not in template
    assert "google" not in script.lower()
    assert "GOOGLE_MAPS_API_KEY" not in env_example


def test_map_page_exposes_plan_drawing_tools(client, django_user_model):
    response = _authenticated_map_page(client, django_user_model, "map-drawing-tools")
    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="plan-drawing-tools"' in page
    assert 'data-project-slug="worksite"' in page
    assert 'data-draw-mode="polygon"' in page
    assert 'id="save-plan-draft"' in page


def test_map_javascript_posts_drawn_feature_collection():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "collectDraftGeometry" in script
    assert "FeatureCollection" in script
    assert "X-CSRFToken" in script
    assert "save-plan-draft" in script
    assert "undo-plan-draft" in script
    assert "redo-plan-draft" in script
    assert "plan-geojson-file" in script
    assert "application/geo+json" in script


def test_map_page_exposes_search_center_inputs(client, django_user_model):
    response = _authenticated_map_page(client, django_user_model, "map-search-center")
    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="location-search-form"' in page
    assert 'id="location-query"' in page
    assert 'id="save-search-center"' in page
    assert 'id="load-nearby-data"' not in page


def test_highway_traffic_has_no_road_filter_and_loads_all_sections(client, django_user_model):
    from pathlib import Path

    page = _authenticated_map_page(client, django_user_model, "map-highway-traffic").content.decode()
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "路況壅塞水準" in page
    assert "省道篩選" not in page
    assert "highway-road-filter" not in script
    assert "loadHighwayRoads" not in script
    assert "/highway-traffic/?limit=300'" in script


def test_traffic_toggle_is_named_and_combines_road_types(client, django_user_model):
    page = _authenticated_map_page(client, django_user_model, "map-traffic-toggle").content.decode()
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "路況壅塞水準" in page
    assert "省道路況壅塞水準" not in page
    assert "/highway-traffic/?limit=300'" in script


@pytest.mark.django_db
def test_traffic_endpoint_merges_highway_and_freeway_sources(client, configured_tdx):
    from datetime import timedelta
    from django.utils import timezone

    now = timezone.now()
    project = Project.objects.create(slug="mixed-traffic", name="Mixed traffic", search_center_latitude=25.0, search_center_longitude=121.5, search_radius_km=10)
    ApiSnapshot.objects.create(service="tdx", kind="freeway_shapes", fetched_at=now, expires_at=now + timedelta(days=1), payload=[{"section_id": "F1", "geometry": "LINESTRING(121.49 25.0,121.51 25.0)"}])
    ApiSnapshot.objects.create(service="tdx", kind="freeway_live", fetched_at=now, expires_at=now + timedelta(days=1), payload=[{"section_id": "F1", "travel_speed": 88, "congestion_level": "1"}])
    ApiSnapshot.objects.create(service="tdx", kind="freeway_sections", fetched_at=now, expires_at=now + timedelta(days=1), payload=[{"section_id": "F1", "road_name": "國道1號", "direction": "N", "section_name": "交流道A~B"}])
    response = client.get(f"/api/projects/{project.slug}/highway-traffic/?limit=300")
    assert response.status_code == 200
    feature = next(item for item in response.json()["features"] if item["properties"]["section_id"] == "F1")
    assert feature["properties"]["road_type"] == "freeway"
    assert feature["properties"]["road_name"] == "國道1號"


@pytest.mark.django_db
def test_project_radar_endpoint_returns_cached_overlay(client, settings):
    settings.CWA_API_KEY = "synthetic-cwa"
    from datetime import timedelta
    from django.utils import timezone

    now = timezone.now()
    project = Project.objects.create(slug="radar", name="Radar", search_center_latitude=25.0, search_center_longitude=121.5, search_radius_km=10)
    ApiSnapshot.objects.create(service="cwa", kind="radar", fetched_at=now, expires_at=now + timedelta(minutes=5), payload=[{"url": "https://example.test/radar.png", "bounds": [[17.75, 115.0], [29.25, 126.5]], "updated_at": now.isoformat()}])
    response = client.get(f"/api/projects/{project.slug}/radar/")
    assert response.status_code == 200
    assert response.json()["overlay"]["url"] == "https://example.test/radar.png"


@pytest.mark.django_db
def test_dynamic_flood_endpoint_returns_wra_overlay(monkeypatch, client):
    from worksite_gis.integrations.wra import WraInundationAdapter
    from worksite_gis.tests.test_dynamic_flood_contract import raster

    project = Project.objects.create(slug="wra-flood", name="WRA flood", search_center_latitude=25.0, search_center_longitude=121.0, search_radius_km=10)
    monkeypatch.setattr(WraInundationAdapter, "fetch_raster", lambda self, region, forecast_hours: raster())
    response = client.get(f"/api/projects/{project.slug}/dynamic-flood/?region=taoyuan&forecastHours=2")
    assert response.status_code == 200
    overlay = response.json()["overlay"]
    assert overlay["bounds"] == [[24.8, 120.9], [25.1, 121.3]]
    assert overlay["image_url"].endswith(f"/dynamic-flood/image/?region=taoyuan&forecastHours=2&batch={overlay['batch_id']}")
    assert client.get(overlay["image_url"]).status_code == 200


def test_map_javascript_renders_weather_and_cctv_layers():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "weather-layer" in script
    assert "cctv-layer" in script
    assert "/nearby/weather-stations/?limit=50" in script
    assert "/nearby/cctv/?limit=50" in script
    assert "if (!project.search_center) return" in script
    assert "renderWeather" in script
    assert "bindTooltip" in script
    assert "precipitation" in script
    assert "wind_speed" in script
    assert "來源暫時無法使用" in script
    assert "氣象過期快取" in script
    assert "影像來源待確認" in script
    assert "cctvSummary" in script
    assert "cctvViewerNode" in script
    assert "if (!viewer || !player || !snapshot || !status) return" in script
    assert "Hls.isSupported()" in script
    assert "loadSource" in script
    assert "attachMedia" in script
    assert "application/vnd.apple.mpegurl" in script
    assert "destroy" in script
    assert "scrollIntoView" in script
    assert "is-focused" in script
    assert "radar-layer" in script
    assert "/radar/" in script
    assert "imageOverlay" in script
    assert "dynamicFloodGroup" in script
    assert "/dynamic-flood/?forecastHours=" in script
    assert "loadDynamicFloodLayer(forecastHours = 2)" in script
    assert "stopDynamicFloodLayer" in script
    assert "function dynamicFloodStatusText(data)" in script
    assert "dynamicFloodStatusText(data)" in script
    assert "data.fetched_at" in script
    assert "error.payload?.reason" in script
    assert "alerts-status" in script
    assert "服務暫時無法使用" in script
    assert "bindTooltip" in script
    assert "所屬機關" not in script


def test_map_javascript_renders_live_train_markers_with_sixty_second_refresh():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "trainGroup" in script
    assert "train_type_name" in script
    assert "direction" in script
    assert "destination" in script
    assert "updateTrainLayer, 60000" in script
    assert "const trainNo = record.train_no || ''" in script
    assert "train-icon span" in __import__("pathlib").Path("home/static/home/css/dashboard.css").read_text(encoding="utf-8")


def test_train_animation_interpolates_smoothly_between_track_points():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "function interpolateTrainPath(path, progress)" in script
    assert "const fraction = scaled - index" in script
    assert "state.marker.setLatLng(interpolateTrainPath(state.path, progress))" in script


def test_train_refresh_reuses_markers_and_labels_source_state():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    render = script.split("function renderTrainMarkers(records)", 1)[1].split("function animateTrainMarkers()", 1)[0]
    assert "const trainMarkers = new Map()" in script
    assert "trainMarkers.get(key)" in render
    assert "trainGroup.clearLayers()" not in render
    assert "runtimeStatusLabel(data.runtime_state)" in script


def test_local_server_launcher_keeps_django_in_foreground():
    launcher = __import__("pathlib").Path("run_local_server.cmd").read_text(encoding="utf-8")
    assert "manage.py runserver 127.0.0.1:8000 --noreload" in launcher
    assert "start /b" not in launcher.lower()


def test_train_status_exposes_separate_fetch_and_render_diagnostics(client, django_user_model):
    page = _authenticated_map_page(client, django_user_model, "map-train-diagnostics").content.decode()
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "列車服務暫時無法使用" in script
    assert "列車圖標繪製失敗" in script
    assert "console.error('train-layer'" in script
    assert "map.js?v=20260825-worksite-search1" in page


def test_train_marker_rendering_rejects_missing_coordinates():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "records.filter(item => item.latitude != null && item.longitude != null && Number.isFinite(Number(item.latitude)) && Number.isFinite(Number(item.longitude)))" in script


def test_train_types_have_distinct_marker_colors():
    css = __import__("pathlib").Path("home/static/home/css/dashboard.css").read_text(encoding="utf-8")
    for train_type in ("自強", "自強3000", "普悠瑪", "太魯閣", "莒光", "區間", "區間快"):
        assert f".train-{train_type} span" in css


def test_train_direction_arrow_is_external_track_aligned_and_keeps_marker_anchor():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    css = __import__("pathlib").Path("home/static/home/css/dashboard.css").read_text(encoding="utf-8")
    assert "headingFromPath" in script
    assert "'</em></span>' + arrow" in script
    assert "'<i class=\"train-direction\"" in script
    assert "iconAnchor: [25, 25]" in script
    assert ".train-direction{position:absolute" in css
    assert ".train-icon.train-區間 span" in css


def test_train_marker_keeps_leaflet_absolute_position_during_pan_and_zoom():
    css = __import__("pathlib").Path("home/static/home/css/dashboard.css").read_text(encoding="utf-8")
    assert ".train-icon{position:relative" not in css
    assert ".leaflet-marker-icon.train-icon{position:absolute" in css


def test_train_position_estimate_uses_next_station_and_timetable():
    from worksite_gis.views import _estimate_train_position

    position = _estimate_train_position({
        "station_name": "甲站", "next_station_name": "乙站",
        "scheduled_departure": "12:00:00", "scheduled_arrival_next": "12:10:00",
        "delay_minutes": 0,
    }, {"甲站": (24.0, 121.0), "乙站": (24.1, 121.2)})
    assert position is not None


    assert 24.0 <= position[0] <= 24.1
    assert 121.0 <= position[1] <= 121.2


def test_train_position_projects_interpolation_onto_rail_geometry():
    from shapely.geometry import LineString
    from worksite_gis.views import _project_point_to_rail

    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    projected = _project_point_to_rail((24.0, 120.05), rail)
    assert projected is not None
    assert abs(projected[0] - 24.02) < 0.004
    assert abs(projected[1] - 120.05) < 0.01


def test_existing_train_coordinate_is_aligned_to_rail_geometry():
    from shapely.geometry import LineString
    from worksite_gis.views import _align_train_position

    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    record = {"latitude": 24.018, "longitude": 120.05}
    aligned = _align_train_position(record, rail)
    assert aligned is not None
    assert aligned[0] > 24.01
    assert aligned[1] < 120.06


def test_far_train_coordinate_is_rejected_instead_of_displayed():
    from shapely.geometry import LineString
    from worksite_gis.views import _sanitize_train_coordinate

    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    record = {"latitude": 25.0, "longitude": 121.0}
    assert _sanitize_train_coordinate(record, rail) is False
    assert record["latitude"] is None
    assert record["longitude"] is None
    assert record["raw_latitude"] == 25.0


def test_tdx_station_id_overrides_stale_next_station_name():
    from worksite_gis.views import _apply_tdx_station_metadata

    record = {"station_id": "1000", "station_name": "松山", "next_station_id": "0990", "next_station_name": "松山"}
    _apply_tdx_station_metadata(record, {"0990": {"station_name": "南港"}})
    assert record["next_station_name"] == "南港"


def test_unmatched_estimate_falls_back_instead_of_showing_off_track_point():
    from shapely.geometry import LineString
    from worksite_gis.views import _apply_estimated_position

    record = {"latitude": 25.0, "longitude": 121.0}
    _apply_estimated_position(record, (25.01, 121.01), [LineString([(120.0, 24.0), (120.05, 24.02)])], (24.9, 120.9))
    assert record["latitude"] == 24.9
    assert record["longitude"] == 120.9
    assert record["position_source"] == "station_route_unverified"


def test_track_estimate_has_bounded_uncertainty_with_source_label():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "train-direction" in script
    assert "track_path" in script
    assert "requestAnimationFrame(animateTrainMarkers)" in script
    assert "系統估算" in script


def test_route_position_interpolates_along_curved_rail_line():
    from shapely.geometry import LineString
    from worksite_gis.views import _estimate_train_position_on_route

    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    record = {"station_name": "甲", "next_station_name": "乙", "scheduled_departure": "12:00:00", "scheduled_arrival_next": "12:10:00", "delay_minutes": 0}
    position = _estimate_train_position_on_route(record, {"甲": (24.0, 120.0), "乙": (24.0, 120.1)}, rail, ratio=0.5)
    assert position is not None
    assert position[0] > 24.01


def test_route_track_path_returns_sampled_coordinates_for_animation():
    from shapely.geometry import LineString
    from worksite_gis.views import _route_track_path

    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    path = _route_track_path({"station_name": "甲", "next_station_name": "乙"}, {"甲": (24.0, 120.0), "乙": (24.0, 120.1)}, rail, samples=8)
    assert len(path) == 8
    assert path[0][0] == 24.0
    assert path[-1][0] == 24.0


def test_live_train_coordinate_also_gets_track_animation_path():
    from shapely.geometry import LineString
    from worksite_gis.views import _attach_track_animation

    record = {
        "latitude": 24.01,
        "longitude": 120.05,
        "station_name": "甲",
        "next_station_name": "乙",
        "scheduled_departure": "12:00:00",
        "scheduled_arrival_next": "12:10:00",
        "delay_minutes": 0,
        "position_source": "track_live",
    }
    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    attached = _attach_track_animation(record, {"甲": (24.0, 120.0), "乙": (24.0, 120.1)}, rail, samples=8)
    assert attached is True
    assert len(record["track_path"]) == 8
    assert record["segment_duration_seconds"] == 600


def test_train_animation_uses_timetable_progress_when_no_gps(monkeypatch):
    from datetime import datetime
    from shapely.geometry import LineString
    from django.utils import timezone
    from worksite_gis.views import _attach_track_animation, _train_progress_ratio

    fixed_now = timezone.make_aware(datetime(2024, 1, 1, 12, 5, 0))
    monkeypatch.setattr("worksite_gis.views.timezone.localtime", lambda: fixed_now)
    record = {
        "latitude": 24.0,
        "longitude": 120.0,
        "station_name": "甲",
        "next_station_name": "乙",
        "scheduled_departure": "12:00:00",
        "scheduled_arrival_next": "12:10:00",
        "delay_minutes": 0,
        "position_source": "track_live",
    }
    expected = _train_progress_ratio(record)
    rail = [LineString([(120.0, 24.0), (120.05, 24.02), (120.1, 24.0)])]
    assert _attach_track_animation(record, {"甲": (24.0, 120.0), "乙": (24.0, 120.1)}, rail, samples=8)
    assert record["track_progress"] == expected


def test_train_progress_accepts_tdx_hour_minute_times(monkeypatch):
    from datetime import datetime
    from django.utils import timezone
    from worksite_gis.views import _train_progress_ratio

    fixed_now = timezone.make_aware(datetime(2024, 1, 1, 9, 1, 0))
    monkeypatch.setattr("worksite_gis.views.timezone.localtime", lambda: fixed_now)
    progress = _train_progress_ratio({
        "scheduled_departure": "09:00",
        "scheduled_arrival_next": "09:05",
        "delay_minutes": 0,
    })
    assert progress == pytest.approx(0.2)


def test_train_segment_duration_uses_timetable_and_handles_midnight():
    from worksite_gis.views import _train_segment_duration_seconds

    assert _train_segment_duration_seconds({
        "scheduled_departure": "09:00",
        "scheduled_arrival_next": "09:05",
    }) == 300
    assert _train_segment_duration_seconds({
        "scheduled_departure": "23:58",
        "scheduled_arrival_next": "00:03",
    }) == 300


@pytest.mark.django_db
def test_train_status_filters_to_worksite_ten_kilometers(monkeypatch, client):
    from worksite_gis.models import Project
    from worksite_gis.integrations.base import AdapterResult
    from django.utils import timezone

    Project.objects.create(slug="worksite", name="WORKSITE", search_center_latitude=24.0, search_center_longitude=121.0, search_radius_km=10)
    records = [
        {"train_no": "near", "latitude": 24.01, "longitude": 121.0},
        {"train_no": "far", "latitude": 24.2, "longitude": 121.0},
    ]
    monkeypatch.setattr("worksite_gis.views.TdxClient.fetch", lambda self: AdapterResult("ok", "tdx", timezone.now(), records))
    monkeypatch.setattr("worksite_gis.views.TdxClient.enrich_train_positions", lambda self, items, include_timetable=False: items)
    monkeypatch.setattr("worksite_gis.views.SnapshotCache.fetch", lambda self, adapter: AdapterResult("ok", "tdx", timezone.now(), records))
    monkeypatch.setattr("worksite_gis.views._sanitize_train_coordinate", lambda record, rails: True)
    response = client.get("/api/projects/worksite/train-status/")
    assert response.status_code == 200
    assert [item["train_no"] for item in response.json()["records"]] == ["near"]


@pytest.mark.django_db
def test_stale_train_snapshot_displays_stale_without_live_enrichment(monkeypatch, client):
    from worksite_gis.models import Project
    from worksite_gis.integrations.base import AdapterResult
    from django.utils import timezone

    Project.objects.create(slug="worksite", name="WORKSITE", search_center_latitude=24.0, search_center_longitude=121.0, search_radius_km=10)
    records = [{"train_no": "near", "latitude": 24.01, "longitude": 121.0}]
    enriched = {"called": False}

    def enrich(_self, items, include_timetable=False):
        enriched["called"] = include_timetable
        items[0]["next_station_id"] = "next"
        return items

    monkeypatch.setattr("worksite_gis.views.SnapshotCache.fetch", lambda self, adapter: AdapterResult("stale", "tdx", timezone.now(), records, True, "temporary failure"))
    monkeypatch.setattr("worksite_gis.views.TdxClient.enrich_train_positions", enrich)
    monkeypatch.setattr("worksite_gis.views._tdx_tra_metadata", lambda client_id, client_secret: ({}, [], [], []))
    monkeypatch.setattr("worksite_gis.views._rail_geometries", lambda: ())
    monkeypatch.setattr("worksite_gis.views._sanitize_train_coordinate", lambda record, rails: True)

    response = client.get("/api/projects/worksite/train-status/")
    assert response.status_code == 200
    assert not enriched["called"]
    assert response.json()["runtime_state"] == "stale"
    assert response.json()["records"][0]["position_freshness"] == "stale"


def test_map_javascript_auto_loads_nearby_after_saving_search_center():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    save_handler = script.split("$('save-search-center').addEventListener", 1)[1].split("function markerIcon", 1)[0]
    assert "renderSearchCenter(); await loadNearby('automatic');" in save_handler
    assert "async function loadNearby(mode)" in script
    assert "$('load-nearby-data').addEventListener('click', loadNearby)" not in script


def test_start_editing_collapses_worksite_setup_panel():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    handler = script.split("function openWorksiteEditor", 1)[1].split("$('start-worksite-edit').addEventListener", 1)[0]
    assert "$('worksite-flow').hidden = true" in handler
    assert "$('worksite-toggle').setAttribute('aria-expanded', 'false')" in handler
    css = __import__("pathlib").Path("home/static/home/css/dashboard.css").read_text(encoding="utf-8")
    assert ".worksite-flow{top:12px" in css


def test_map_exposes_right_cctv_viewer_and_map_markers(client, django_user_model):
    page = _authenticated_map_page(client, django_user_model, "map-cctv-viewer").content.decode()
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert 'id="cctv-viewer"' in page
    assert 'id="cctv-list"' not in page
    assert "cctvGroup" in script
    assert "cctv-viewer" in script
    assert "stopPreviousCctv" in script
    assert "function ensureCctvWidgets()" in script
    assert "function renderCctv(records, errorMsg, state)" in script
    assert "renderCctv = function" not in script
    assert "cctvListNode" not in script
    assert "searchCenterGroup" in script
    assert "cctvGroup.clearLayers" in script


def test_map_javascript_builds_project_urls_from_data_attribute():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "function currentSlug()" in script
    assert "'/api/projects/' + currentSlug()" in script
    assert "fetch('/api/projects/worksite/" not in script


def test_map_javascript_does_not_insert_api_text_with_inner_html():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")

    assert ".innerHTML" not in script


def test_map_javascript_loads_spatial_layers_by_current_bbox():
    script = __import__("pathlib").Path("home/static/home/js/map.js").read_text(encoding="utf-8")

    assert "toBBoxString" in script
    assert "delivery === 'bbox'" in script


def test_map_javascript_provides_geologic_sensitive_viewport_control():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")

    assert "geologic_sensitive_areas" in script
    assert "/api/layers/geologic_sensitive_areas/viewport/" in script
    assert "function refreshGeologicSensitiveLayer" in script
    assert "AbortController" in script
    assert "map.getBounds()" in script
    assert "setTimeout" in script


def test_map_javascript_does_not_create_legacy_geologic_layer_control():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    loader = script.split("Promise.all([loadProjects(), loadLayers()])", 1)[1].split("function weatherMetric", 1)[0]

    assert "geologic_sensitive_areas" in loader
    assert "node.remove()" in loader


def test_map_javascript_initializes_geologic_control_when_projects_request_fails():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "synchronizeGeologicSensitiveControl" in script
    assert "new MutationObserver(synchronizeGeologicSensitiveControl)" in script


def test_geologic_viewport_errors_do_not_fallback_or_clear_existing_layer():
    from pathlib import Path

    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    viewport = script.split("function geologicViewportErrorMessage", 1)[1].split("function createGeologicSensitiveControl", 1)[0]

    assert "401" in viewport
    assert "403" in viewport
    assert "503" in viewport
    assert "fallback" not in viewport.lower()
    assert "map.removeLayer(geologicSensitiveLayer)" in viewport


@pytest.mark.django_db
def test_layer_detail_uses_recorded_metadata_output_when_catalog_path_is_stale(client):
    from pathlib import Path

    output = Path(".tmp-tests") / "nature-reserves-layer-detail.geojson"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    LayerCatalog.objects.create(
        slug="nature_reserves",
        name="nature_reserves",
        category="protection",
        path=str(output.parent / "stale" / output.name),
        metadata={"output": str(output)},
    )

    response = client.get("/api/layers/nature_reserves/")

    assert response.status_code == 200
    assert response.json() == {"type": "FeatureCollection", "features": []}


@pytest.mark.django_db
def test_layer_detail_uses_catalog_output_when_database_paths_are_stale(client):
    from pathlib import Path
    from django.test import override_settings

    root = (Path(".tmp-tests") / "catalog-output-fallback").resolve()
    catalog_dir = root / "data" / "catalog"
    output = root / "data" / "processed" / "gis" / "protection" / "nature_reserves.geojson"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    (catalog_dir / "layers.json").write_text(json.dumps({"nature_reserves": {"output": str(output)}}), encoding="utf-8")
    LayerCatalog.objects.create(
        slug="nature_reserves",
        name="nature_reserves",
        category="protection",
        path=str(root / "stale" / "nature_reserves.geojson"),
        metadata={"output": str(root / "also-stale" / "nature_reserves.geojson")},
    )

    with override_settings(BASE_DIR=root):
        response = client.get("/api/layers/nature_reserves/")

    assert response.status_code == 200
    assert response.json() == {"type": "FeatureCollection", "features": []}


def test_map_javascript_provides_static_flood_selector_and_keeps_dynamic_live_only():
    from pathlib import Path
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "function mergeFloodLayerControl" in script
    assert "flood_6h_150mm" in script
    assert "flood_12h_400mm" in script
    assert "flood_24h_500mm" in script
    assert "flood_650mm_24h" in script
    selector = script.split("function mergeFloodLayerControl", 1)[1].split("setInterval(mergeFloodLayerControl", 1)[0]
    assert "dynamicOption" not in selector
    assert "loadDynamicFloodLayer" not in selector
    assert "map.on('moveend'" in script


def test_map_javascript_uses_flood_toggle_without_none_option():
    from pathlib import Path
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    selector = script.split("function mergeFloodLayerControl", 1)[1].split("setInterval(mergeFloodLayerControl", 1)[0]
    assert "flood-toggle" in selector
    assert "toggle.checked" in selector
    assert "select.value" in selector
    assert "none.textContent" not in selector


@pytest.mark.django_db
def test_maintainer_status_pages_use_safe_dom_rendering(client, django_user_model):
    user = django_user_model.objects.create_user(username="safe-maintainer", password="secret", is_staff=True)
    client.force_login(user)

    for url in ("/maintainer/gis-layers/", "/maintainer/api-services/"):
        response = client.get(url)
        page = response.content.decode()
        assert response.status_code == 200
        assert "tr.innerHTML" not in page
        assert "textContent" in page


def test_offline_tile_endpoint_serves_existing_tile(client, tmp_path):
    from django.test import override_settings

    root = tmp_path / "archive" / "CH_3857_Vect_OpenData" / "CH_3857_Vect_OpenData"
    tile = root / "L00" / "R00000000" / "C00000000.jpg"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"tile")

    with override_settings(OFFLINE_TILE_ROOT=tmp_path):
        response = client.get("/api/tiles/0/0/0.jpg")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"


@pytest.mark.django_db
def test_status_endpoint_reports_catalog_and_basemap(client, tmp_path):
    from django.test import override_settings

    LayerCatalog.objects.create(slug="status_layer", name="Status layer", category="hazards", path="data/processed/gis/hazards/debris_flow.geojson")
    tile_root = tmp_path / "status-tiles"
    tile = tile_root / "archive" / "CH_3857_Vect_OpenData" / "CH_3857_Vect_OpenData" / "L00" / "R00000000" / "C00000000.jpg"
    tile.parent.mkdir(parents=True, exist_ok=True)
    tile.write_bytes(b"tile")
    with override_settings(OFFLINE_TILE_ROOT=tile_root):
        response = client.get("/api/status/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "worksite_gis"
    assert payload["layers"]["count"] >= 1
    assert payload["basemap"]["available"] is True


@pytest.mark.django_db
def test_status_reports_basemap_unavailable_when_manifest_has_no_tile_files(client, tmp_path):
    from django.test import override_settings

    manifest_dir = tmp_path / "data" / "processed" / "basemap"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "offline_tiles_manifest.json").write_text(
        json.dumps({"file_count": 535631}), encoding="utf-8"
    )
    with override_settings(
        OFFLINE_TILE_ROOT=tmp_path / "missing-tiles",
        DATA_PROCESSED_DIR=tmp_path / "data" / "processed",
    ):
        response = client.get("/api/status/")

    assert response.status_code == 200
    assert response.json()["basemap"] == {"available": False, "file_count": 0}


def test_catalog_path_resolves_stale_windows_paths_to_data_root(tmp_path):
    from django.test import override_settings
    from worksite_gis.views import _catalog_path

    data_root = tmp_path / "data"
    with override_settings(DATA_ROOT=data_root):
        resolved = _catalog_path(
            r"R:\archived\data\processed\gis\transport\rail_lines.geojson"
        )

    assert resolved == data_root / "processed" / "gis" / "transport" / "rail_lines.geojson"


@pytest.mark.django_db
def test_status_endpoint_reports_api_service_health(client):
    ApiServiceConfig.objects.create(name="Weather", slug="weather", status="healthy")
    ApiServiceConfig.objects.create(name="CCTV", slug="cctv", status="error")
    response = client.get("/api/status/")
    assert response.status_code == 200
    assert response.json()["api_services"] == {"count": 2, "healthy": 1, "degraded": 0, "error": 1, "unknown": 0}


@pytest.mark.django_db
def test_status_endpoint_reports_processed_data_validation(client):
    response = client.get("/api/status/")
    assert response.status_code == 200
    validation = response.json()["processed_data"]
    assert validation["available"] is True
    assert validation["count"] > 0
    assert validation["valid"] == validation["count"]
    assert validation["invalid"] == 0


@pytest.mark.django_db
def test_status_reports_synthetic_validation_catalog(client, settings, tmp_path):
    settings.DATA_CATALOG_DIR = tmp_path
    report = tmp_path / "validation_report.json"
    report.write_text(json.dumps({"valid_fixture": {"ok": True}, "invalid_fixture": {"ok": False}}))
    response = client.get("/api/status/")
    assert response.status_code == 200
    assert response.json()["processed_data"] == {"available": True, "count": 2, "valid": 1, "invalid": 1}


def test_index_is_worksite_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Worksite GIS — Engineering Geology Screening" in response.content.decode()
    assert "alert-panel" in response.content.decode()
    assert "Asia/Taipei" in response.content.decode()


def test_index_exposes_alert_history_controls(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="alert-history"' in page
    assert 'id="alert-history-list"' in page
    assert "/api/alerts/history/" in page


def test_index_displays_api_service_summary(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "api_services" in page
    assert "healthy" in page


def test_index_refreshes_platform_status(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "refreshPlatformStatus" in page
    assert page.count("fetch('/api/status/')") == 1
    assert "setInterval(refreshPlatformStatus" in page


def test_index_exposes_processed_data_status(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="data-status"' in page
    assert "processed_data" in page


@pytest.mark.django_db
def test_maintainer_gis_layers_requires_staff(client):
    response = client.get("/api/maintainer/gis-layers/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_maintainer_gis_layers_returns_health_fields(client, django_user_model):
    user = django_user_model.objects.create_user(username="maintainer", password="secret", is_staff=True)
    client.force_login(user)
    LayerCatalog.objects.create(slug="admin_layer", name="Admin layer", category="hazards", format="geojson", path="data/processed/gis/hazards/debris_flow.geojson", crs="EPSG:4326", feature_count=3)
    response = client.get("/api/maintainer/gis-layers/")
    assert response.status_code == 200
    item = response.json()["layers"][0]
    assert {"slug", "source", "path", "crs", "published", "health"}.issubset(item)


@pytest.mark.django_db
def test_maintainer_data_tables_lists_json_csv_outputs(client, django_user_model):
    user = django_user_model.objects.create_user(username="table-maintainer", password="secret", is_staff=True)
    client.force_login(user)
    from pathlib import Path
    root = Path(".test-artifacts") / "data-tables"
    root.mkdir(parents=True, exist_ok=True)
    table = root / "weather_stations.json"
    csv_copy = root / "weather_stations.csv"
    table.write_text('{"kind":"weather_stations","records":[{"id":"1","name":"Station"}]}', encoding="utf-8")
    csv_copy.write_text("id,name\n1,Station\n", encoding="utf-8")
    LayerCatalog.objects.create(
        slug="weather_stations", name="Weather stations", category="api_seed", format="json",
        path=str(table), feature_count=1, metadata={"csv_output": str(csv_copy)},
    )
    response = client.get("/api/maintainer/data-tables/")
    assert response.status_code == 200
    item = response.json()["tables"][0]
    assert item["slug"] == "weather_stations"
    assert item["record_count"] == 1
    assert item["csv_available"] is True


@pytest.mark.django_db
def test_maintainer_data_table_detail_supports_preview_and_csv_download(client, django_user_model):
    user = django_user_model.objects.create_user(username="table-detail-maintainer", password="secret", is_staff=True)
    client.force_login(user)
    from pathlib import Path
    root = Path(".test-artifacts") / "data-tables"
    root.mkdir(parents=True, exist_ok=True)
    table = root / "water.json"
    csv_copy = root / "water.csv"
    table.write_text('{"kind":"water","records":[{"id":"1","level":2},{"id":"2","level":3}]}', encoding="utf-8")
    csv_copy.write_text("id,level\n1,2\n2,3\n", encoding="utf-8")
    LayerCatalog.objects.create(slug="water", name="Water", category="api_seed", format="json", path=str(table), feature_count=2, metadata={"csv_output": str(csv_copy)})
    preview = client.get("/api/maintainer/data-tables/water/")
    assert preview.status_code == 200
    assert preview.json()["records"] == [{"id": "1", "level": 2}, {"id": "2", "level": 3}]
    paged = client.get("/api/maintainer/data-tables/water/?limit=1&offset=1")
    assert paged.status_code == 200
    assert paged.json()["records"] == [{"id": "2", "level": 3}]
    assert paged.json()["pagination"] == {"limit": 1, "offset": 1, "total": 2, "has_next": False}
    searched = client.get("/api/maintainer/data-tables/water/?q=3")
    assert searched.status_code == 200
    assert searched.json()["records"] == [{"id": "2", "level": 3}]
    assert searched.json()["pagination"]["total"] == 1
    download = client.get("/api/maintainer/data-tables/water/?format=csv")
    assert download.status_code == 200
    assert download["Content-Type"].startswith("text/csv")
    assert b"id,level" in download.content


@pytest.mark.django_db
def test_maintainer_data_tables_page_requires_staff_and_exposes_endpoint(client, django_user_model):
    assert client.get("/maintainer/data-tables/").status_code in {302, 403}
    user = django_user_model.objects.create_user(username="table-page", password="secret", is_staff=True)
    client.force_login(user)
    response = client.get("/maintainer/data-tables/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "data-table-list" in page
    assert "/api/maintainer/data-tables/" in page
    assert "data-table-preview" in page
    assert "data-table-prev" in page
    assert "data-table-next" in page


def test_maintainer_data_tables_page_uses_safe_dom_rendering(client, django_user_model):
    user = django_user_model.objects.create_user(username="table-ui-safe", password="pw", is_staff=True)
    client.force_login(user)

    response = client.get("/maintainer/data-tables/")

    assert response.status_code == 200
    page = response.content.decode("utf-8")
    assert "textContent" in page
    assert "replaceChildren" in page
    assert "head.innerHTML" not in page
    assert "body.innerHTML" not in page
    assert "tr.innerHTML" not in page


@pytest.mark.django_db
def test_maintainer_gis_page_is_available(client, django_user_model):
    user = django_user_model.objects.create_user(username="page-maintainer", password="secret", is_staff=True)
    client.force_login(user)
    response = client.get("/maintainer/gis-layers/")
    assert response.status_code == 200
    assert "GIS 圖層管理" in response.content.decode()


@pytest.mark.django_db
def test_maintainer_api_services_returns_status(client, django_user_model):
    user = django_user_model.objects.create_user(username="api-maintainer", password="secret", is_staff=True)
    client.force_login(user)
    ApiServiceConfig.objects.create(name="Weather", slug="weather", status="unknown")
    response = client.get("/api/maintainer/api-services/")
    assert response.status_code == 200
    assert response.json()["services"][0]["slug"] == "weather"


@pytest.mark.django_db
def test_maintainer_api_page_is_available(client, django_user_model):
    user = django_user_model.objects.create_user(username="api-page", password="secret", is_staff=True)
    client.force_login(user)
    response = client.get("/maintainer/api-services/")
    assert response.status_code == 200
    assert "API 管理" in response.content.decode()


@pytest.mark.django_db
def test_maintainer_api_page_refreshes_service_status(client, django_user_model):
    user = django_user_model.objects.create_user(username="api-refresh", password="secret", is_staff=True)
    client.force_login(user)
    response = client.get("/maintainer/api-services/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "setInterval" in page
    assert "/api/maintainer/api-services/" in page


@pytest.mark.django_db
def test_projects_endpoint_returns_map_defaults(client, django_user_model):
    user = django_user_model.objects.create_user(username="project-reader")
    client.force_login(user)
    Project.objects.create(slug="worksite", name="WORKSITE PoC", center_latitude=23.7, center_longitude=120.9, default_zoom=7)
    response = client.get("/api/projects/")
    assert response.status_code == 200
    assert response.json()["projects"][0]["slug"] == "worksite"


@pytest.mark.django_db
def test_project_create_update_and_archive_require_staff(client, django_user_model):
    forbidden = client.post("/api/projects/", data={"slug": "new-site", "name": "新工區"}, content_type="application/json")
    user = django_user_model.objects.create_user(username="project-admin", password="secret", is_staff=True)
    client.force_login(user)
    created = client.post("/api/projects/", data={"slug": "new-site", "name": "新工區", "center_latitude": 24.985556, "center_longitude": 121.310278}, content_type="application/json")
    updated = client.patch("/api/projects/new-site/", data={"name": "新工區 A"}, content_type="application/json")
    archived = client.post("/api/projects/new-site/archive/")

    assert forbidden.status_code == 401
    assert created.status_code == 201
    assert updated.json()["name"] == "新工區 A"
    assert archived.json()["status"] == "archived"


@pytest.mark.django_db
def test_external_data_endpoints_return_bounded_normalized_result(client, monkeypatch):
    from worksite_gis.integrations.base import AdapterResult
    from django.utils import timezone

    class Cache:
        def __init__(self, *args, **kwargs): pass
        def fetch(self, adapter):
            return AdapterResult("ok", adapter.source, timezone.now(), [{"station_id": str(i)} for i in range(300)])

    monkeypatch.setattr("worksite_gis.views.SnapshotCache", Cache)
    weather = client.get("/api/weather/?limit=9999")

    assert weather.status_code == 200
    assert weather.json()["status"] == "ok"
    assert len(weather.json()["records"]) == 200


@pytest.mark.django_db
def test_project_search_center_rejects_anonymous_trial_save(client):
    Project.objects.create(slug="worksite", name="WORKSITE PoC")

    response = client.post("/api/projects/worksite/search-center/", data={"latitude": 24.1, "longitude": 120.7, "radius_km": 10}, content_type="application/json")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "authentication required"
    assert Project.objects.get(slug="worksite").setup_status == "unlocated"


@pytest.mark.django_db
def test_project_search_center_validates_and_persists(client, django_user_model):
    user = django_user_model.objects.create_user(username="planner", password="secret")
    Project.objects.create(slug="worksite", name="WORKSITE PoC", owner=user)
    client.force_login(user)
    response = client.post("/api/projects/worksite/search-center/", data={"latitude": 24.1, "longitude": 120.7, "radius_km": 10}, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["search_center"]["latitude"] == 24.1


@pytest.mark.django_db
def test_project_search_center_rejects_invalid_coordinates(client, django_user_model):
    user = django_user_model.objects.create_user(username="invalid-center", password="secret")
    Project.objects.create(slug="worksite", name="WORKSITE PoC", owner=user)
    client.force_login(user)
    response = client.post("/api/projects/worksite/search-center/", data={"latitude": 95, "longitude": 120}, content_type="application/json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_project_nearby_uses_saved_search_center_and_limit(client):
    project = Project.objects.create(slug="worksite", name="WORKSITE PoC", search_center_latitude=23.7, search_center_longitude=120.9, search_radius_km=10)
    StationSeed.objects.create(kind="weather", external_id="near", name="Near", latitude=23.71, longitude=120.9)
    StationSeed.objects.create(kind="weather", external_id="far", name="Far", latitude=24.5, longitude=120.9)
    response = client.get(f"/api/projects/{project.slug}/nearby/")
    assert response.status_code == 200
    assert [item["external_id"] for item in response.json()["results"]] == ["near"]


@pytest.mark.django_db
def test_project_nearby_rejects_non_numeric_limit(client):
    Project.objects.create(slug="worksite", name="WORKSITE PoC", search_center_latitude=23.7, search_center_longitude=120.9)

    response = client.get("/api/projects/worksite/nearby/?limit=not-a-number")

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "limit must be numeric"


@pytest.mark.django_db
def test_public_nearby_caps_results_at_two_hundred(client):
    StationSeed.objects.bulk_create([
        StationSeed(kind="weather", external_id=f"station-{index}", name=f"Station {index}", latitude=23.7, longitude=120.9)
        for index in range(205)
    ])

    response = client.get("/api/nearby/?lat=23.7&lng=120.9&radius_km=1&limit=9999")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 200


@pytest.mark.django_db
def test_project_nearby_filters_by_station_kind(client):
    project = Project.objects.create(slug="worksite", name="WORKSITE PoC", search_center_latitude=23.7, search_center_longitude=120.9, search_radius_km=10)
    StationSeed.objects.create(kind="weather", external_id="weather-1", name="Weather", latitude=23.71, longitude=120.9)
    StationSeed.objects.create(kind="cctv", external_id="cctv-1", name="CCTV", latitude=23.72, longitude=120.9)
    response = client.get(f"/api/projects/{project.slug}/nearby/?kind=weather")
    assert response.status_code == 200
    assert [item["kind"] for item in response.json()["results"]] == ["weather"]


@pytest.mark.django_db
def test_current_alerts_excludes_cleared_alerts(client):
    from django.utils import timezone
    Alert.objects.create(source="test", severity="red", title="Active", occurred_at=timezone.now())
    Alert.objects.create(source="test", severity="yellow", title="Cleared", occurred_at=timezone.now(), cleared_at=timezone.now())
    response = client.get("/api/alerts/current/")
    assert response.status_code == 200
    assert [item["title"] for item in response.json()["alerts"]] == ["Active"]


@pytest.mark.django_db
def test_sync_alerts_command_populates_current_alerts_and_deduplicates(monkeypatch, client):
    from datetime import timedelta

    from django.core.management import call_command
    from django.utils import timezone

    from worksite_gis.integrations.base import AdapterResult

    monkeypatch.setattr("worksite_gis.views.settings.CWA_API_KEY", "synthetic")
    now = timezone.now()
    record = {
        "external_id": "cwa-1",
        "source": "CWA",
        "severity": "red",
        "title": "大雨｜桃園市",
        "payload": {"external_id": "cwa-1", "location": "桃園市"},
        "occurred_at": (now - timedelta(hours=1)).isoformat(),
        "valid_from": (now - timedelta(minutes=45)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    monkeypatch.setattr("worksite_gis.views.CwaAlertAdapter.fetch", lambda self: AdapterResult("ok", "CWA", now, [record]))

    call_command("sync_alerts")
    call_command("sync_alerts")
    response = client.get("/api/alerts/current/")

    assert response.status_code == 200
    assert [alert["title"] for alert in response.json()["alerts"]] == ["大雨｜桃園市"]
    assert Alert.objects.count() == 1


@pytest.mark.django_db
def test_alert_history_supports_limit(client):
    from datetime import timedelta
    from django.utils import timezone
    Alert.objects.create(source="test", severity="red", title="One", occurred_at=timezone.now())
    Alert.objects.create(source="test", severity="yellow", title="Old", occurred_at=timezone.now() - timedelta(hours=13))
    response = client.get("/api/alerts/history/?limit=1")
    assert response.status_code == 200
    assert len(response.json()["alerts"]) == 1


@pytest.mark.django_db
def test_alert_history_defaults_to_recent_twelve_hours(client):
    from datetime import timedelta
    from django.utils import timezone

    Alert.objects.create(source="test", severity="red", title="Recent", occurred_at=timezone.now() - timedelta(hours=2))
    Alert.objects.create(source="test", severity="yellow", title="Old", occurred_at=timezone.now() - timedelta(hours=13))
    response = client.get("/api/alerts/history/")
    assert response.status_code == 200
    assert [item["title"] for item in response.json()["alerts"]] == ["Recent"]


@pytest.mark.django_db
def test_project_plans_endpoint_returns_plan_metadata(client, django_user_model):
    user = django_user_model.objects.create_user(username="plan-reader")
    client.force_login(user)
    project = Project.objects.create(slug="worksite", name="WORKSITE PoC")
    Plan.objects.create(project=project, name="施工分區", purpose="工區規劃", created_by=user)
    response = client.get("/api/projects/worksite/plans/")
    assert response.status_code == 200
    assert response.json()["plans"][0]["name"] == "施工分區"


@pytest.mark.django_db
def test_project_plans_create_requires_staff(client):
    Project.objects.create(slug="worksite", name="WORKSITE PoC")
    response = client.post("/api/projects/worksite/plans/", data={"name": "施工分區"}, content_type="application/json")
    assert response.status_code == 401


@pytest.mark.django_db
def test_project_plans_create_and_update_as_staff(client, django_user_model):
    user = django_user_model.objects.create_user(username="plan-maintainer", password="secret", is_staff=True)
    client.force_login(user)
    Project.objects.create(slug="worksite", name="WORKSITE PoC")
    response = client.post(
        "/api/projects/worksite/plans/",
        data={"name": "施工分區", "purpose": "工區規劃", "status": "active", "geometry": {"type": "FeatureCollection", "features": []}},
        content_type="application/json",
    )
    assert response.status_code == 201
    plan_id = response.json()["plan"]["id"]
    response = client.put(f"/api/projects/worksite/plans/{plan_id}/", data={"name": "施工分區 v2", "version": 2}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": "1"})
    assert response.status_code == 200
    assert response.json()["plan"]["name"] == "施工分區 v2"
    assert response.json()["plan"]["version"] == 2


@pytest.mark.django_db
def test_project_plans_create_rejects_invalid_status(client, django_user_model):
    user = django_user_model.objects.create_user(username="plan-validator", password="secret", is_staff=True)
    client.force_login(user)
    Project.objects.create(slug="worksite", name="WORKSITE PoC")
    response = client.post("/api/projects/worksite/plans/", data={"name": "施工分區", "status": "unknown"}, content_type="application/json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_maintainer_plans_page_requires_staff(client):
    response = client.get("/maintainer/plans/")
    assert response.status_code in {302, 403}


@pytest.mark.django_db
def test_maintainer_plans_page_is_available(client, django_user_model):
    user = django_user_model.objects.create_user(username="plans-page", password="secret", is_staff=True)
    client.force_login(user)
    response = client.get("/maintainer/plans/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "plan-editor" in page
    assert 'data-plans-endpoint="/api/projects/worksite/plans/"' in page
    assert "plan-cancel" in page
