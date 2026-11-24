from django.conf import settings
from django.core.management.base import BaseCommand

from worksite_gis.data_pipeline.loaders import load_catalog_into_db


class Command(BaseCommand):
    help = "Load data/catalog/layers.json into SQLite models."

    def handle(self, *args, **options):
        count = load_catalog_into_db(settings.DATA_CATALOG_DIR / "layers.json")
        self.stdout.write(self.style.SUCCESS(f"loaded {count} catalog layers"))
