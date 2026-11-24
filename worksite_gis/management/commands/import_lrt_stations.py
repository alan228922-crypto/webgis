import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


CODE_RE = re.compile(r"^([A-Za-z]+)(\d+)([A-Za-z]*)")


def normalize_code(value):
    value = re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip().upper()
    match = CODE_RE.match(value)
    if not match:
        return value
    prefix, number, suffix = match.groups()
    return f"{prefix}{int(number):02d}{suffix}"


class Command(BaseCommand):
    help = "匯入高雄環狀輕軌車站 CSV，合併至 MRT 車站 GeoJSON"

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        if not csv_path.exists():
            raise CommandError(f"找不到 CSV：{csv_path}")

        grouped = defaultdict(list)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"車站編號", "Longitude", "Latitude"}
            if not required.issubset(reader.fieldnames or set()):
                raise CommandError(f"CSV 缺少必要欄位：{sorted(required - set(reader.fieldnames or []))}")
            for row in reader:
                code = normalize_code(row["車站編號"])
                try:
                    longitude = float(row["Longitude"])
                    latitude = float(row["Latitude"])
                except (TypeError, ValueError) as exc:
                    raise CommandError(f"車站 {code} 座標無效") from exc
                if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
                    raise CommandError(f"車站 {code} 座標超出範圍")
                grouped[code].append((longitude, latitude, row.get("車站中文名稱", ""), row.get("車站英文名稱", "")))

        path = settings.DATA_PROCESSED_DIR / "gis" / "transport" / "mrt_stations.geojson"
        if not path.exists():
            raise CommandError(f"找不到 MRT 車站圖層：{path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        features = [
            feature
            for feature in data.get("features", [])
            if not (
                (feature.get("properties") or {}).get("operator") == "高雄捷運"
                and str((feature.get("properties") or {}).get("station_code", "")).startswith("C")
            )
        ]
        for code in sorted(grouped):
            points = grouped[code]
            longitude = sum(point[0] for point in points) / len(points)
            latitude = sum(point[1] for point in points) / len(points)
            station_name = next((point[2] for point in points if point[2]), "")
            station_name_en = next((point[3] for point in points if point[3]), "")
            properties = {"station_code": code, "operator": "高雄捷運"}
            if station_name:
                properties["station_name"] = station_name
            if station_name_en:
                properties["station_name_en"] = station_name_en
            features.append({
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
            })
        data["features"] = features
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"已匯入 {len(grouped)} 個高雄輕軌車站（CSV {sum(map(len, grouped.values()))} 筆，已合併重複方向座標）"))
