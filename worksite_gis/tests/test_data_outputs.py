import json
from pathlib import Path


def test_expected_transport_outputs_are_indexed():
    catalog_path = Path("data/catalog/layers.json")
    if not catalog_path.exists():
        return
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert {"tra_stations", "rail_lines", "mrt_stations", "mrt_lines", "highways"} <= set(catalog)


def test_nature_reserves_catalog_path_matches_existing_output():
    catalog = json.loads(Path("data/catalog/layers.json").read_text(encoding="utf-8-sig"))
    validation = json.loads(Path("data/catalog/validation_report.json").read_text(encoding="utf-8-sig"))
    expected = Path("data/processed/gis/protection/nature_reserves.geojson").resolve()
    assert expected.exists()
    assert Path(catalog["nature_reserves"]["output"]).resolve() == expected
    assert Path(validation["nature_reserves"]["path"]).resolve() == expected


def test_mrt_stations_keep_only_station_codes():
    data = json.loads(Path("data/processed/gis/transport/mrt_stations.geojson").read_text(encoding="utf-8"))
    assert data["features"]
    assert all(feature["geometry"]["type"] == "Point" for feature in data["features"])
    assert all(set(feature["properties"]) <= {"station_code", "operator", "station_name", "station_name_en", "line_name"} for feature in data["features"])
    assert all({"station_code", "operator"} <= set(feature["properties"]) for feature in data["features"])
    assert len({(feature["properties"]["operator"], feature["properties"]["station_code"]) for feature in data["features"]}) == len(data["features"])
    assert {feature["properties"]["operator"] for feature in data["features"]} >= {"臺北捷運", "臺中捷運", "高雄捷運", "桃園捷運"}


def test_mrt_lines_include_lrt_source():
    data = json.loads(Path("data/processed/gis/transport/mrt_lines.geojson").read_text(encoding="utf-8"))
    assert any(feature["properties"].get("source_layer") == "lrt" for feature in data["features"])


def test_mrt_stations_include_kaohsiung_lrt_csv_codes():
    data = json.loads(Path("data/processed/gis/transport/mrt_stations.geojson").read_text(encoding="utf-8"))
    kaohsiung_codes = {
        feature["properties"]["station_code"]
        for feature in data["features"]
        if feature["properties"]["operator"] == "高雄捷運"
    }
    assert {"C01", "C21A", "C37"} <= kaohsiung_codes
    assert len({code for code in kaohsiung_codes if code.startswith("C")}) == 38


def test_mrt_stations_have_display_names():
    data = json.loads(Path("data/processed/gis/transport/mrt_stations.geojson").read_text(encoding="utf-8"))
    assert all(feature["properties"].get("station_name") for feature in data["features"])


def test_mrt_stations_include_imported_new_taipei_lines():
    data = json.loads(Path("data/processed/gis/transport/mrt_stations.geojson").read_text(encoding="utf-8"))
    line_names = {feature["properties"].get("line_name") for feature in data["features"]}
    assert {"淡海輕軌", "新北環狀線"} <= line_names
    assert all(feature["properties"].get("station_code") for feature in data["features"] if feature["properties"].get("line_name") in {"淡海輕軌", "新北環狀線"})


def test_danhai_line_variants_use_station_color():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "'淡海輕軌綠山線': '#E5554F'" in script
    assert "'淡海輕軌藍海線': '#E5554F'" in script
    assert "String(properties.MRTCODE || '').includes('淡海輕軌')) return '#E5554F'" in script


def test_tra_station_uses_pill_marker_renderer():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    css = Path("home/static/home/css/mrt.css").read_text(encoding="utf-8")
    assert "function traStationIcon" in script
    assert "tra-station-marker" in script
    assert "tra_stations" in script
    assert ".tra-station-marker" in css


def test_tra_line_and_station_controls_are_merged():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "function mergeTraLayerControls" in script
    assert "台鐵路線／車站" in script
    assert "data-tra-network-control" in script


def test_tra_station_marker_uses_line_color_and_station_name():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    css = Path("home/static/home/css/mrt.css").read_text(encoding="utf-8")
    assert "function traStationIcon(code, name, scale, color)" in script
    assert "traStationIcon(code, label, scale, '#2182CC')" in script
    assert "三鶯線" in script
    assert "淡海輕軌" in script
    assert "安坑輕軌" in script
    assert "border: 4px solid var(--tra-line-color" in css
    assert "function traStationLabel" in script
    assert "traStationIcon(code, label, scale, '#2182CC')" in script


def test_mrt_layer_color_override_does_not_recurse():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "const baseTransportLayerColor = transportLayerColor;" not in script
    assert "return baseTransportLayerColor(item, feature);" in script


def test_mrt_station_tooltip_includes_station_metadata():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "function mrtStationTooltip" in script
    assert "properties.station_name" in script
    assert ".bindTooltip(mrtStationTooltip(" in script
    assert "Math.max(.15, Math.min(1.6" in script


def test_station_markers_hide_when_map_scale_reaches_five_kilometres():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "STATION_VISIBILITY_DISTANCE_METERS = 5000" in script
    assert "SCALE_REFERENCE_PIXELS = 100" in script
    assert "containerPointToLatLng" in script
    assert "function stationsVisibleAtCurrentScale" in script
    assert "function updateStationVisibility" in script
    assert "mrtStationMarkers.forEach" in script
    assert "traStationMarkers.forEach" in script
