import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from worksite_gis.data_pipeline.convert import convert_excel_workbook, convert_vector
from worksite_gis.data_pipeline.run_import import convert_api_workbooks, run_import


def test_convert_vector_reprojects_to_wgs84():
    root = Path(".test-artifacts")
    root.mkdir(exist_ok=True)
    source = root / "stations.shp"
    gdf = gpd.GeoDataFrame({"name": ["test"]}, geometry=[Point(300000, 2760000)], crs="EPSG:3826")
    gdf.to_file(source)
    result = convert_vector(source, root / "stations.geojson", "tra_stations")
    assert result.crs == "EPSG:4326"
    assert result.feature_count == 1


def test_convert_excel_workbook_preserves_sheet_and_writes_csv_copy():
    root = Path(".test-artifacts")
    root.mkdir(exist_ok=True)
    source = root / "workbook.xlsx"
    output = root / "workbook.json"
    csv_output = root / "workbook.csv"
    with pd.ExcelWriter(source) as writer:
        pd.DataFrame({"id": ["A1"], "name": ["Alpha"], "latitude": [23.7], "longitude": [120.9]}).to_excel(writer, sheet_name="Active", index=False)
        pd.DataFrame({"id": ["B2"], "name": ["Beta"], "latitude": [24.1], "longitude": [121.5]}).to_excel(writer, sheet_name="Retired", index=False)

    result = convert_excel_workbook(source, output, "stations", csv_output=csv_output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result.feature_count == 2
    assert payload == {
        "kind": "stations",
        "records": [
            {"id": "A1", "name": "Alpha", "latitude": 23.7, "longitude": 120.9, "_sheet": "Active"},
            {"id": "B2", "name": "Beta", "latitude": 24.1, "longitude": 121.5, "_sheet": "Retired"},
        ],
    }
    assert "Alpha" in csv_output.read_text(encoding="utf-8-sig")


def test_convert_api_workbooks_creates_platform_json_and_csv_outputs():
    root = Path(".test-artifacts") / "api-workbooks"
    api_root = root / "api資料表"
    (api_root / "水位").mkdir(parents=True, exist_ok=True)
    (api_root / "CCTV").mkdir(parents=True, exist_ok=True)
    samples = {
        api_root / "氣象站位置.xlsx": {"站號": ["W1"], "站名": ["Weather"], "緯度": [23.7], "經度": [120.9]},
        api_root / "台鐵車站編號.xlsx": {"車站編碼": ["R1"], "車站名稱": ["Rail"]},
        api_root / "車站與編號.xlsx": {"車站編碼": ["S1"], "車站名稱": ["Station"]},
        api_root / "資料庫 Inventory.xlsx": {"name": ["Inventory"], "status": ["keep"]},
        api_root / "水位" / "河川水位測站站況.xlsx": {"observatoryidentifier": ["L1"], "observatoryname": ["Water"]},
        api_root / "CCTV" / "Taiwan_Real_CCTV_Full.xlsx": {"編號": ["C1"], "位置名稱": ["Camera"], "緯度": [23.8], "經度": [121.0]},
        api_root / "CCTV" / "twipcam_台灣CCTV大清單_2026-08-04.xlsx": {"編號": ["C2"], "位置名稱": ["Camera 2"], "緯度": [23.9], "經度": [121.1]},
    }
    for path, data in samples.items():
        pd.DataFrame(data).to_excel(path, index=False)

    results = convert_api_workbooks(root, root / "data")

    assert {item["slug"] for item in results} == {
        "weather_stations", "rail_station_codes", "station_codes", "source_inventory",
        "water_level_stations", "cctv_stations", "twipcam_cctv_catalog",
    }
    cctv = json.loads((root / "data" / "processed" / "api_seed" / "cctv_stations.json").read_text(encoding="utf-8"))
    assert cctv["records"][0]["位置名稱"] == "Camera"
    assert (root / "data" / "processed" / "tables_csv" / "water_level_stations.csv").exists()


def test_run_import_keeps_excel_conversion_when_optional_vector_has_no_crs():
    root = Path(".test-artifacts") / "run-import-optional-vector"
    gis_root = root / "GIS圖資"
    api_root = root / "api資料表"
    gis_root.mkdir(parents=True, exist_ok=True)
    api_root.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame({"name": ["bad"]}, geometry=[Point(120.9, 23.7)]).to_file(gis_root / "bad_2024.shp")
    gpd.GeoDataFrame({"name": ["bad"]}, geometry=[Point(120.9, 23.7)]).to_file(gis_root / "24h650.shp")
    pd.DataFrame({"站號": ["W1"], "站名": ["Weather"], "緯度": [23.7], "經度": [120.9]}).to_excel(api_root / "氣象站位置.xlsx", index=False)

    report = run_import(root, root / "data", root / "tiles")

    assert "weather_stations" in {item["slug"] for item in report["layers"]}
    assert report["failed"] == []
