from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from worksite_gis.data_pipeline.simplify import simplify_geojson
from worksite_gis.data_pipeline.spatial_index import build_spatial_index
from worksite_gis.data_pipeline.validate import validate_output
from worksite_gis.models import LayerCatalog


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else settings.BASE_DIR / path


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _update_catalog(slug: str, index_path: Path, result, validation) -> None:
    indexed_validation = asdict(validation)
    indexed_validation["path"] = str(index_path)
    indexed_validation["format"] = "spatial"
    layers_path = settings.DATA_CATALOG_DIR / "layers.json"
    if layers_path.exists():
        layers = json.loads(layers_path.read_text(encoding="utf-8-sig"))
        if slug in layers:
            layers[slug].update({
                "format": "spatial",
                "output": str(index_path),
                "output_hash": result.output_hash,
                "feature_count": result.feature_count,
                "bbox": result.bbox,
                "validation": indexed_validation,
            })
            _write_json_atomic(layers_path, layers)
    validation_path = settings.DATA_CATALOG_DIR / "validation_report.json"
    validation_payload = {}
    if validation_path.exists():
        validation_payload = json.loads(validation_path.read_text(encoding="utf-8-sig"))
    validation_payload[slug] = indexed_validation
    _write_json_atomic(validation_path, validation_payload)


class Command(BaseCommand):
    help = "Build simplified display GeoJSON and an RTree risk index. Dry-run unless --apply is supplied."

    def add_arguments(self, parser):
        parser.add_argument("--slug", action="append", required=True)
        parser.add_argument("--tolerance", type=float, default=0.0005)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        selected = list(dict.fromkeys(options["slug"]))
        layers = list(LayerCatalog.objects.filter(
            category__in=("hazards", "protection"), format__in=("geojson", "spatial")
        ))
        by_slug = {layer.slug: layer for layer in layers}
        missing = [slug for slug in selected if slug not in by_slug]
        if missing:
            raise CommandError(f"unknown risk layer(s): {', '.join(missing)}")
        jobs = []
        for slug in selected:
            layer = by_slug[slug]
            spatial_metadata = layer.metadata.get("spatial", {}) if isinstance(layer.metadata, dict) else {}
            source = _resolve_path(spatial_metadata.get("full_geometry_source", layer.path))
            display = source.with_name(source.stem + ".display.geojson")
            jobs.append((layer, source, display))
            self.stdout.write(f"{slug}: {source} -> {display}")
        self.stdout.write(f"risk index: {settings.SPATIAL_INDEX_PATH}")
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("dry-run: no files or catalog records changed"))
            return

        results = {}
        validations = {}
        for layer, source, display in jobs:
            if not source.exists():
                raise CommandError(f"source does not exist: {source}")
            result = simplify_geojson(source, display, tolerance=options["tolerance"])
            validation = validate_output(display, "vector")
            if not validation.ok or validation.feature_count != layer.feature_count:
                raise CommandError(
                    f"validation failed for {layer.slug}: {validation.errors or 'feature count mismatch'}"
                )
            results[layer.slug] = result
            validations[layer.slug] = validation

        index_inputs = []
        for layer in layers:
            selected_job = next((job for job in jobs if job[0].slug == layer.slug), None)
            if selected_job:
                source = selected_job[1]
            else:
                spatial_metadata = layer.metadata.get("spatial", {}) if isinstance(layer.metadata, dict) else {}
                source = _resolve_path(spatial_metadata.get("full_geometry_source", layer.path))
            if not source.exists():
                raise CommandError(f"risk index source does not exist: {source}")
            index_inputs.append((layer.slug, layer.category, source))
        index_report = build_spatial_index(index_inputs, settings.SPATIAL_INDEX_PATH)

        with transaction.atomic():
            for layer, source, display in jobs:
                metadata = dict(layer.metadata) if isinstance(layer.metadata, dict) else {}
                metadata["spatial"] = {
                    "indexed": True,
                    "index_path": str(settings.SPATIAL_INDEX_PATH),
                    "full_geometry_source": str(source),
                    "display_tolerance": options["tolerance"],
                }
                indexed_validation = asdict(validations[layer.slug])
                indexed_validation["path"] = str(settings.SPATIAL_INDEX_PATH)
                indexed_validation["format"] = "spatial"
                metadata["validation"] = indexed_validation
                layer.path = str(settings.SPATIAL_INDEX_PATH)
                layer.format = "spatial"
                layer.metadata = metadata
                layer.save(update_fields=["path", "format", "metadata", "updated_at"])
                _update_catalog(
                    layer.slug, Path(settings.SPATIAL_INDEX_PATH), results[layer.slug], validations[layer.slug]
                )
        self.stdout.write(self.style.SUCCESS(
            f"optimized {len(jobs)} layer(s); indexed {index_report['feature_count']} features"
        ))
