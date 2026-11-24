from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook

from worksite_gis.models import CctvCamera, CctvImportBatch


ALIASES = {
    "camera_id": ("編號", "攝影機編號", "設備編號", "cctvid", "cameraid", "id"),
    "city": ("縣市", "城市", "city"),
    "road_name": ("道路名稱", "道路", "roadname", "road"),
    "location_name": ("位置名稱", "位置", "地點", "locationname", "location"),
    "longitude": ("經度", "longitude", "lon", "lng"),
    "latitude": ("緯度", "latitude", "lat"),
    "stream_url": ("即時影像網址", "影像網址", "網址", "streamurl", "url"),
}


def normalize(value):
    value = "".join(str(value or "").strip().lower().replace("_", "").split())
    return value.removeprefix("content.")


def header_map(values):
    normalized = {normalize(value): index for index, value in enumerate(values)}
    result = {field: next((normalized[normalize(alias)] for alias in aliases if normalize(alias) in normalized), None) for field, aliases in ALIASES.items()}
    english = {"camera_id": "cctvserialnumber", "city": "cctvcountiesandcities", "location_name": "cctvlocationname", "longitude": "cctvlongitude", "latitude": "cctvlatitude", "stream_url": "cctvlive-videourl"}
    for field, key in english.items():
        if result[field] is None:
            result[field] = normalized.get(key)
    return result


def media_type(url):
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"): return "hls"
    if path.endswith((".jpg", ".jpeg", ".png")): return "image"
    if path.endswith(".mjpg") or path.endswith(".mjpeg"): return "mjpeg"
    return "external"


class Command(BaseCommand):
    help = "匯入 CCTV Excel 快照；以 provider + camera_id 冪等更新。"

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--source", required=True)

    def handle(self, *args, **options):
        path = Path(options["file"]).resolve()
        provider = options["source"].strip()
        if not path.exists() or path.suffix.lower() != ".xlsx":
            raise CommandError("--file 必須是存在的 .xlsx 檔")
        workbook = load_workbook(path, read_only=True, data_only=True)
        created = updated = invalid = 0
        errors = []
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            headers = next(rows, None)
            if not headers:
                continue
            mapping = header_map(headers)
            required = ("camera_id", "longitude", "latitude")
            if any(mapping[field] is None for field in required):
                continue
            for row_number, row in enumerate(rows, start=2):
                try:
                    camera_id = str(row[mapping["camera_id"]] or "").strip()
                    longitude = float(row[mapping["longitude"]])
                    latitude = float(row[mapping["latitude"]])
                    if not camera_id or not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                        raise ValueError("無效編號或 WGS84 座標")
                    def value(field):
                        index = mapping.get(field)
                        return str(row[index] or "").strip() if index is not None and index < len(row) else ""
                    url = value("stream_url")
                    _, was_created = CctvCamera.objects.update_or_create(
                        provider=provider,
                        camera_id=camera_id,
                        defaults={"source_type": sheet.title, "city": value("city"), "road_name": value("road_name"), "location_name": value("location_name"), "longitude": longitude, "latitude": latitude, "stream_url": url, "media_type": media_type(url), "source_file": path.name},
                    )
                    created += int(was_created)
                    updated += int(not was_created)
                except (TypeError, ValueError, IndexError) as exc:
                    invalid += 1
                    if len(errors) < 20:
                        errors.append({"sheet": sheet.title, "row": row_number, "error": str(exc)})
        CctvImportBatch.objects.create(source=provider, source_file=path.name, imported_count=created, updated_count=updated, invalid_count=invalid, error_summary=errors)
        self.stdout.write(self.style.SUCCESS(f"匯入完成：新增 {created}、更新 {updated}、無效 {invalid}"))
