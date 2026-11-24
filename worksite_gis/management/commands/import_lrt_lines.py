import json
from pathlib import Path

import geopandas as gpd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "匯入 LRT 輕軌線形資料至捷運路線圖層"

    def handle(self, *args, **options):
        source = next(settings.BASE_DIR.glob("data/source/lrt/輕軌捷運_1150409/LRT_1150409.shp"), None)
        output = settings.DATA_PROCESSED_DIR / "gis" / "transport" / "mrt_lines.geojson"
        if source is None or not source.exists():
            raise CommandError("找不到 LRT_1150409.shp")
        current = json.loads(output.read_text(encoding="utf-8"))
        current["features"] = [feature for feature in current.get("features", []) if (feature.get("properties") or {}).get("source_layer") != "lrt"]
        frame = gpd.read_file(source).to_crs("EPSG:4326")
        for record in json.loads(frame.to_json(na="null"))["features"]:
            properties = record.get("properties") or {}
            properties = {
                "OBJECTID": properties.get("OBJECTID"),
                "MRTID": properties.get("LRTID"),
                "MRTSYS": properties.get("LRTSYS"),
                "MRTCODE": properties.get("LRTCODE"),
                "MRTTYPE": properties.get("LRTTYPE"),
                "STATUS": properties.get("STATUS"),
                "source_layer": "lrt",
            }
            record["properties"] = properties
            current["features"].append(record)
        output.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"已匯入 {len(frame)} 筆輕軌線形資料"))
