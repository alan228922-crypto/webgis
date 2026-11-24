"""Import authoritative station additions into the platform MRT GeoJSON."""

import argparse
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from pyproj import Transformer


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/processed/gis/transport/mrt_stations.geojson"
WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)


def feature(code, operator, name, lon, lat, line_name):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
        "properties": {
            "station_code": code,
            "operator": operator,
            "station_name": name,
            "station_name_en": "",
            "line_name": line_name,
        },
    }


def fetch_danhai():
    load_dotenv(ROOT / ".env")
    token = requests.post(
        "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["TDX_CLIENT_ID"],
            "client_secret": os.environ["TDX_CLIENT_SECRET"],
        },
        timeout=30,
    ).json()["access_token"]
    response = requests.get(
        "https://tdx.transportdata.tw/api/basic/v2/Rail/Metro/Station/NTDLRT",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    additions = []
    for item in response.json():
        position = item["StationPosition"]
        name = item["StationName"].get("Zh_tw") or item["StationID"]
        additions.append(feature(item["StationID"], "新北捷運", name, position["PositionLon"], position["PositionLat"], "淡海輕軌"))
    return additions


def load_circular(source_path):
    source = json.loads(source_path.read_text(encoding="utf-8"))
    codes = {
        "新北產業園區站": "Y01",
        "幸福站": "Y02",
        "頭前庄站": "Y03",
        "新埔民生站": "Y04",
        "板橋站": "Y05",
        "板新站": "Y06",
        "中原站": "Y07",
        "橋和站": "Y08",
        "中和站": "Y09",
        "景平站": "Y10",
        "景安站": "Y11",
        "十四張站": "Y12",
        "秀朗橋站": "Y13",
        "大坪林站": "Y14",
    }
    candidates = {}
    for item in source.get("features", []):
        props = item.get("properties", {})
        name = props.get("NAME")
        if name not in codes:
            continue
        if name not in candidates or "位於" in str(props.get("LOC", "")):
            candidates[name] = item
    additions = []
    for name, code in codes.items():
        item = candidates.get(name)
        if not item:
            continue
        x, y = item["geometry"]["coordinates"]
        lon, lat = WGS84.transform(x, y)
        additions.append(feature(code, "新北捷運", name.removesuffix("站"), lon, lat, "新北環狀線"))
    return additions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--circular-source", type=Path, required=True)
    circular_source = parser.parse_args().circular_source
    if not circular_source.is_file():
        parser.error(f"source file not found: {circular_source}")
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    existing = {(f["properties"].get("operator"), f["properties"].get("station_code")): f for f in data["features"]}
    additions = fetch_danhai() + load_circular(circular_source)
    for item in additions:
        key = (item["properties"]["operator"], item["properties"]["station_code"])
        existing[key] = item
    data["features"] = list(existing.values())
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"imported={len(additions)} total={len(data['features'])}")


if __name__ == "__main__":
    main()
