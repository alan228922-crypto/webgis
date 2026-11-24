import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from worksite_gis.data_pipeline.cleanup import apply_cleanup, plan_cleanup
from worksite_gis.data_pipeline.sources import discover_vector_sources


class Command(BaseCommand):
    help = "Remove only validated source sidecars; dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        root = settings.BASE_DIR
        converted = list(discover_vector_sources(root).values())
        converted.append(root / "api資料表" / "帳號密碼.xlsx")
        plan = plan_cleanup(root, converted)
        self.stdout.write(json.dumps({"remove": [str(p) for p in plan.remove], "keep": [str(p) for p in plan.keep]}, ensure_ascii=False, indent=2))
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("dry-run only; no files removed"))
            return
        if options["confirm"] != "WORKSITE-CLEANUP":
            raise CommandError("--confirm WORKSITE-CLEANUP is required")
        removed = apply_cleanup(plan, root)
        (settings.DATA_CATALOG_DIR / "deletion_manifest.json").write_text(json.dumps(removed, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"removed {len(removed)} files"))
