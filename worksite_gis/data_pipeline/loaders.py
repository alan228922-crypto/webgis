from __future__ import annotations

import json
from pathlib import Path

from django.db import transaction

from worksite_gis.models import LayerCatalog, StationSeed


STATION_KIND_MAP = {
    "weather_stations": "weather",
    "cctv_stations": "cctv",
    "twipcam_cctv_catalog": "cctv",
    "water_level_stations": "water_level",
    "rail_station_codes": "rail",
    "station_codes": "rail",
}

ID_FIELDS = ("id", "station_id", "\u7ad9\u865f", "\u7de8\u865f", "\u8eca\u7ad9\u7de8\u78bc", "observatoryidentifier")
NAME_FIELDS = ("name", "station_name", "\u7ad9\u540d", "\u4f4d\u7f6e\u540d\u7a31", "\u8eca\u7ad9\u540d\u7a31", "observatoryname")
LATITUDE_FIELDS = ("latitude", "lat", "\u7def\u5ea6")
LONGITUDE_FIELDS = ("longitude", "lng", "\u7d93\u5ea6")


def _first_value(row: dict, fields: tuple[str, ...]):
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            return value
    return None


@transaction.atomic
def load_catalog_into_db(catalog_path: Path) -> int:
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    refresh_kinds = set()
    for slug, item in catalog.items():
        if item.get("format") == "json":
            refresh_kinds.add(slug)
            refresh_kinds.add(STATION_KIND_MAP.get(slug, slug))
    if refresh_kinds:
        StationSeed.objects.filter(kind__in=refresh_kinds).delete()
    count = 0
    for slug, item in catalog.items():
        LayerCatalog.objects.update_or_create(
            slug=slug,
            defaults={
                "name": item.get("name", slug),
                "category": item.get("category", "unknown"),
                "format": item.get("format", "geojson"),
                "path": item["output"],
                "source": item.get("source", ""),
                "crs": item.get("crs") or "",
                "feature_count": item.get("feature_count", 0),
                "metadata": item,
            },
        )
        count += 1
        if item.get("format") == "json":
            payload = json.loads(Path(item["output"]).read_text(encoding="utf-8"))
            for index, row in enumerate(payload.get("records", [])):
                external_id = str(_first_value(row, ID_FIELDS) or index)
                name = str(_first_value(row, NAME_FIELDS) or external_id)
                latitude = _first_value(row, LATITUDE_FIELDS)
                longitude = _first_value(row, LONGITUDE_FIELDS)
                if latitude is None or longitude is None:
                    continue
                StationSeed.objects.update_or_create(
                    kind=STATION_KIND_MAP.get(slug, slug),
                    external_id=external_id,
                    defaults={"name": name, "latitude": float(latitude), "longitude": float(longitude), "properties": row},
                )
    return count
