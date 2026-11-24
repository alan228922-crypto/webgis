import json
from pathlib import Path

import pytest

from worksite_gis.data_pipeline.loaders import load_catalog_into_db
from worksite_gis.models import StationSeed


@pytest.mark.django_db
def test_load_catalog_maps_platform_station_kinds_and_chinese_columns():
    root = Path(".test-artifacts") / "loader"
    api_seed = root / "processed" / "api_seed"
    catalog_path = root / "catalog" / "layers.json"
    api_seed.mkdir(parents=True, exist_ok=True)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    weather_path = api_seed / "weather_stations.json"
    cctv_path = api_seed / "cctv_stations.json"
    weather_path.write_text(json.dumps({"records": [{"站號": "W1", "站名": "Weather", "緯度": 23.7, "經度": 120.9}]}, ensure_ascii=False), encoding="utf-8")
    cctv_path.write_text(json.dumps({"records": [{"編號": "C1", "位置名稱": "Camera", "緯度": 23.8, "經度": 121.0}]}, ensure_ascii=False), encoding="utf-8")
    catalog_path.write_text(json.dumps({
        "weather_stations": {"name": "weather_stations", "category": "api_seed", "format": "json", "output": str(weather_path), "feature_count": 1},
        "cctv_stations": {"name": "cctv_stations", "category": "api_seed", "format": "json", "output": str(cctv_path), "feature_count": 1},
    }, ensure_ascii=False), encoding="utf-8")
    StationSeed.objects.create(kind="weather_stations", external_id="old", name="Old", latitude=0, longitude=0)

    load_catalog_into_db(catalog_path)

    assert StationSeed.objects.get(kind="weather", external_id="W1").name == "Weather"
    assert StationSeed.objects.get(kind="cctv", external_id="C1").name == "Camera"
    assert not StationSeed.objects.filter(kind="weather_stations").exists()
