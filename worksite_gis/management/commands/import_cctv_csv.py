import csv
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError

from worksite_gis.models import CctvCamera, CctvImportBatch


def media_type(url):
    path = urlparse(url).path.lower()
    if path.endswith((".jpg", ".jpeg", ".png", ".mjpg", ".mjpeg")) or "snapshot" in path:
        return "image"
    if path.endswith(".m3u8"):
        return "hls"
    return "external" if url else ""


class Command(BaseCommand):
    help = "Import CCTV locations and live URLs from CSV."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--source", default="twipcam")

    def handle(self, *args, **options):
        path = Path(options["file"]).resolve()
        if not path.exists() or path.suffix.lower() != ".csv":
            raise CommandError("--file must be an existing .csv file")
        created = updated = invalid = 0
        errors = []
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp950")
        import io
        with io.StringIO(text, newline="") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                try:
                    camera_id = str(row.get("CCTV_serial_number") or "").strip()
                    latitude = float(row["CCTV_latitude"])
                    longitude = float(row["CCTV_longitude"])
                    if not camera_id or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        raise ValueError("invalid CCTV id or WGS84 coordinates")
                    url = str(row.get("CCTV_Live-video_URL") or "").strip()
                    _, was_created = CctvCamera.objects.update_or_create(
                        provider=options["source"], camera_id=camera_id,
                        defaults={"source_type": "csv", "city": str(row.get("CCTV_Counties_and_cities") or "").strip(), "location_name": str(row.get("CCTV_Location_Name") or camera_id).strip(), "latitude": latitude, "longitude": longitude, "stream_url": url, "media_type": media_type(url), "source_file": path.name},
                    )
                    created += int(was_created)
                    updated += int(not was_created)
                except (KeyError, TypeError, ValueError) as exc:
                    invalid += 1
                    if len(errors) < 20:
                        errors.append({"row": row_number, "error": str(exc)})
        CctvImportBatch.objects.create(source=options["source"], source_file=str(path), imported_count=created, updated_count=updated, invalid_count=invalid, error_summary=errors)
        self.stdout.write(self.style.SUCCESS(f"CCTV CSV imported: created={created}, updated={updated}, invalid={invalid}"))
