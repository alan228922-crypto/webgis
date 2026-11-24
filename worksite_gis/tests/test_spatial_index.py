import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from worksite_gis.data_pipeline.spatial_index import build_spatial_index, query_bbox, query_point
from worksite_gis.models import LayerCatalog


def _write_layer(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


def _polygon_feature(name, west, south, east, north):
    return {
        "type": "Feature",
        "properties": {"name": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
        },
    }


def _artifact_dir(name):
    root = Path(".test-artifacts") / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_spatial_index_queries_exact_geometry_and_category():
    root = _artifact_dir("spatial-index-query")
    hazard = root / "hazard.geojson"
    protection = root / "protection.geojson"
    index = root / "risk.sqlite3"
    _write_layer(hazard, [_polygon_feature("flood", 120, 23, 121, 24)])
    _write_layer(protection, [_polygon_feature("reserve", 121, 24, 122, 25)])

    report = build_spatial_index([
        ("flood", "hazards", hazard),
        ("reserve", "protection", protection),
    ], index)

    assert report == {"feature_count": 2, "layers": {"flood": 1, "reserve": 1}}
    assert query_point(index, 120.5, 23.5, ("hazards",)) == [{
        "slug": "flood",
        "category": "hazards",
        "properties": {"name": "flood"},
    }]
    assert query_point(index, 120.5, 23.5, ("protection",)) == []
    assert query_point(index, 119.0, 22.0, ("hazards", "protection")) == []


def test_spatial_index_returns_clipped_display_features_for_bbox():
    root = _artifact_dir("spatial-index-bbox")
    source = root / "hazard.geojson"
    index = root / "risk.sqlite3"
    _write_layer(source, [
        _polygon_feature("inside", 120, 23, 121, 24),
        _polygon_feature("outside", 122, 25, 123, 26),
    ])
    build_spatial_index([("flood", "hazards", source)], index)

    features = query_bbox(index, "flood", (120.25, 23.25, 120.75, 23.75), limit=10)

    assert len(features) == 1
    assert features[0]["type"] == "Feature"
    assert features[0]["properties"] == {"name": "inside"}
    coordinates = features[0]["geometry"]["coordinates"][0]
    assert min(point[0] for point in coordinates) >= 120.25
    assert max(point[0] for point in coordinates) <= 120.75


def test_spatial_index_build_failure_preserves_previous_index():
    root = _artifact_dir("spatial-index-atomic")
    valid = root / "valid.geojson"
    invalid = root / "invalid.geojson"
    index = root / "risk.sqlite3"
    _write_layer(valid, [_polygon_feature("existing", 120, 23, 121, 24)])
    invalid.write_text("not-json", encoding="utf-8")
    build_spatial_index([("existing", "hazards", valid)], index)

    with pytest.raises(json.JSONDecodeError):
        build_spatial_index([("broken", "hazards", invalid)], index)

    assert query_point(index, 120.5, 23.5, ("hazards",))[0]["slug"] == "existing"


@pytest.mark.django_db
def test_optimize_spatial_layers_dry_run_does_not_write_outputs():
    root = _artifact_dir("optimize-dry-run")
    source = root / "flood.geojson"
    index = root / "risk.sqlite3"
    display = root / "flood.display.geojson"
    _write_layer(source, [_polygon_feature("flood", 120, 23, 121, 24)])
    index.unlink(missing_ok=True)
    display.unlink(missing_ok=True)
    LayerCatalog.objects.create(slug="flood", name="Flood", category="hazards", format="geojson", path=str(source))

    with override_settings(SPATIAL_INDEX_PATH=index):
        call_command("optimize_spatial_layers", "--slug", "flood")

    assert not index.exists()
    assert not display.exists()


@pytest.mark.django_db
def test_optimize_spatial_layers_apply_updates_catalog_after_validation():
    root = _artifact_dir("optimize-apply")
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(exist_ok=True)
    source = root / "flood.geojson"
    index = root / "risk.sqlite3"
    _write_layer(source, [_polygon_feature("flood", 120, 23, 121, 24)])
    layer = LayerCatalog.objects.create(
        slug="flood", name="Flood", category="hazards", format="geojson", path=str(source), feature_count=1
    )
    (catalog_dir / "layers.json").write_text(json.dumps({
        "flood": {"slug": "flood", "output": str(source), "feature_count": 1, "validation": {"ok": True}}
    }), encoding="utf-8")
    (catalog_dir / "validation_report.json").write_text(json.dumps({"flood": {"ok": True}}), encoding="utf-8")

    with override_settings(SPATIAL_INDEX_PATH=index, DATA_CATALOG_DIR=catalog_dir):
        call_command("optimize_spatial_layers", "--slug", "flood", "--apply")

    layer.refresh_from_db()
    saved_catalog = json.loads((catalog_dir / "layers.json").read_text(encoding="utf-8"))
    assert layer.format == "spatial"
    assert Path(layer.path) == index
    assert index.exists()
    assert saved_catalog["flood"]["format"] == "spatial"
    assert saved_catalog["flood"]["output"] == str(index)
    assert saved_catalog["flood"]["validation"]["path"] == str(index)
    assert layer.metadata["validation"]["format"] == "spatial"
    assert saved_catalog["flood"]["validation"]["ok"] is True
    assert query_point(index, 120.5, 23.5, ("hazards",))[0]["slug"] == "flood"
