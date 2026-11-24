from django.core.management.base import BaseCommand
from django.conf import settings

from worksite_gis.data_pipeline.catalog import build_offline_tiles_manifest


class Command(BaseCommand):
    help = "Build the offline tile manifest and ensure catalog directories exist."

    def add_arguments(self, parser):
        parser.add_argument("--write-report", action="store_true")

    def handle(self, *args, **options):
        output = settings.DATA_PROCESSED_DIR / "basemap" / "offline_tiles_manifest.json"
        manifest = build_offline_tiles_manifest(settings.OFFLINE_TILE_ROOT, output)
        self.stdout.write(self.style.SUCCESS(f"indexed {manifest['file_count']} offline tiles"))
