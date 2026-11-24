import json
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from shapely.geometry import Point, shape
from shapely.ops import nearest_points


CODE_RE = re.compile(r"(?<![A-Z0-9])(?:A|BL|BR|G|O|O|R|RK|C)\d{1,3}[A-Z]?(?![A-Z0-9])", re.IGNORECASE)


def normalize_code(value):
    match = re.fullmatch(r"([A-Z]+)(\d{1,3})([A-Z]?)", value.upper())
    if not match:
        return value.upper()
    prefix, number, suffix = match.groups()
    return f"{prefix}{int(number):02d}{suffix}"


def operator_for(raw):
    if "高雄捷運" in raw:
        return "高雄捷運"
    if "臺中捷運" in raw:
        return "臺中捷運"
    if "桃園機場捷運" in raw:
        return "桃園捷運"
    if "臺北捷運" in raw or "台北捷運" in raw or "新北捷運" in raw:
        return "臺北捷運"
    return "其他"


class Command(BaseCommand):
    help = "移除捷運出入口資料，只保留每個車站代號一個代表點"

    def handle(self, *args, **options):
        path = settings.DATA_PROCESSED_DIR / "gis" / "transport" / "mrt_stations.geojson"
        if not path.exists():
            raise CommandError(f"找不到捷運車站圖層：{path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        grouped = defaultdict(list)
        for feature in data.get("features", []):
            properties = feature.get("properties") or {}
            raw = " ".join(str(properties.get(key) or "") for key in ("MARKNAME2", "MARKNAME1"))
            coordinates = (feature.get("geometry") or {}).get("coordinates")
            matches = CODE_RE.findall(raw)
            if "臺中捷運" in raw:
                matches.extend(re.findall(r"_(1\d{2})(?:\b|$)", raw))
            if not matches or not isinstance(coordinates, list) or len(coordinates) < 2:
                continue
            for code in matches:
                grouped[(operator_for(raw), normalize_code(code))].append((float(coordinates[0]), float(coordinates[1]), raw))
        line_path = settings.DATA_PROCESSED_DIR / "gis" / "transport" / "mrt_lines.geojson"
        line_geometries = []
        if line_path.exists():
            line_data = json.loads(line_path.read_text(encoding="utf-8"))
            line_geometries = [shape(feature["geometry"]) for feature in line_data.get("features", []) if feature.get("geometry")]
        features = []
        for (operator, code) in sorted(grouped):
            points = grouped[(operator, code)]
            longitude = sum(point[0] for point in points) / len(points)
            latitude = sum(point[1] for point in points) / len(points)
            point = Point(longitude, latitude)
            if line_geometries:
                nearest_line = min(line_geometries, key=lambda line: line.distance(point))
                snapped = nearest_points(point, nearest_line)[1]
                longitude, latitude = snapped.x, snapped.y
            features.append({
                "type": "Feature",
                "properties": {"station_code": code, "operator": operator},
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
            })
        if not features:
            raise CommandError("沒有找到可保留的捷運車站代號")
        data["features"] = features
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"捷運車站已整理：{len(features)} 個代號；移除 {sum(len(v) for v in grouped.values()) - len(features)} 筆出入口"))
