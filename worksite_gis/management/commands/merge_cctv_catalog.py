from django.core.management.base import BaseCommand

from worksite_gis.models import CctvCamera, StationSeed


class Command(BaseCommand):
    help = "Merge local CCTV station catalog into the canonical CctvCamera table."

    def handle(self, *args, **options):
        created = updated = skipped = 0
        existing = list(CctvCamera.objects.all())
        by_id = {camera.camera_id: camera for camera in existing}
        by_point = {(round(camera.latitude, 4), round(camera.longitude, 4)): camera for camera in existing}
        for station in StationSeed.objects.filter(kind="cctv"):
            props = station.properties if isinstance(station.properties, dict) else {}
            url = str(props.get("即時影像網址") or props.get("stream_url") or props.get("影像網址") or "").strip()
            match = by_id.get(station.external_id) or by_point.get((round(station.latitude, 4), round(station.longitude, 4)))
            if match:
                changed = False
                if url and not match.stream_url:
                    match.stream_url = url
                    match.media_type = "image" if "snapshot" in url.lower() or "cctv-ss" in url.lower() else "external"
                    changed = True
                if not match.location_name and station.name:
                    match.location_name = station.name
                    changed = True
                if changed:
                    match.save(update_fields=["stream_url", "media_type", "location_name", "imported_at"])
                    updated += 1
                else:
                    skipped += 1
                continue
            camera = CctvCamera.objects.create(provider="local catalog", camera_id=station.external_id, source_type="station_seed", location_name=station.name, latitude=station.latitude, longitude=station.longitude, stream_url=url, media_type="image" if "snapshot" in url.lower() or "cctv-ss" in url.lower() else ("external" if url else ""), source_file=props.get("source_file", ""), metadata=props)
            existing.append(camera)
            by_id[camera.camera_id] = camera
            by_point[(round(camera.latitude, 4), round(camera.longitude, 4))] = camera
            created += 1
        self.stdout.write(self.style.SUCCESS(f"CCTV merged: created={created}, updated={updated}, skipped={skipped}"))
