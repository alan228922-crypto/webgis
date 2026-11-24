"""Convert all available static flood scenarios into bbox-queryable layers."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from worksite_gis.data_pipeline.convert import convert_vector_collection
from worksite_gis.data_pipeline.spatial_index import build_spatial_index


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data/processed/gis/hazards"
CATALOG_PATH = ROOT / "data/catalog/layers.json"

SCENARIOS = {
    (6, 150): "flood_6h_150mm",
    (6, 250): "flood_6h_250mm",
    (6, 350): "flood_6h_350mm",
    (12, 200): "flood_12h_200mm",
    (12, 300): "flood_12h_300mm",
    (12, 400): "flood_12h_400mm",
    (24, 200): "flood_24h_200mm",
    (24, 350): "flood_24h_350mm",
    (24, 500): "flood_24h_500mm",
    (24, 650): "flood_24h_650mm",
}


def scenario_key(stem: str):
    text = stem.lower().replace("_", "")
    duration = next((hours for hours in (24, 12, 6) if re.search(rf"(?<![0-9])0?{hours}(?:h|hr)", text)), None)
    depth = next((mm for mm in (650, 500, 400, 350, 300, 250, 200, 150) if str(mm) in text), None)
    return (duration, depth) if (duration, depth) in SCENARIOS else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    source_root = parser.parse_args().source_root
    if not source_root.is_dir():
        parser.error(f"source directory not found: {source_root}")
    grouped = {key: [] for key in SCENARIOS}
    for source in source_root.rglob("*.shp"):
        key = scenario_key(source.stem)
        if key:
            grouped[key].append(source)
    missing = [key for key, sources in grouped.items() if not sources]
    if missing:
        raise SystemExit(f"missing scenarios: {missing}")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    results = {}
    for key, slug in SCENARIOS.items():
        sources = sorted(grouped[key])
        geojson = OUTPUT_ROOT / f"{slug}.geojson"
        index = ROOT / "data/processed/gis/risk_index.sqlite3" if slug == "flood_24h_650mm" and (ROOT / "data/processed/gis/risk_index.sqlite3").exists() else OUTPUT_ROOT / f"{slug}.sqlite3"
        if geojson.exists() and index.exists():
            connection = sqlite3.connect(index)
            stored_slug = "flood_650mm_24h" if slug == "flood_24h_650mm" else slug
            feature_count = connection.execute("SELECT COUNT(*) FROM risk_features WHERE layer_slug = ?", (stored_slug,)).fetchone()[0]
            connection.close()
            print(f"{slug}: reuse existing index features={feature_count}")
            result = None
        elif geojson.exists():
            result = None
            feature_count = 0
            print(f"{slug}: reuse existing GeoJSON; building missing index")
        else:
            # The Tainan 207-12 source package omits .prj files.  Its companion
            # flood layers use TWD97 / TM2 (EPSG:3826), so apply that explicit
            # documented fallback rather than allowing an implicit CRS guess.
            result = convert_vector_collection(sources, geojson, slug, missing_crs="EPSG:3826")
        index_slug = "flood_650mm_24h" if slug == "flood_24h_650mm" else slug
        if not index.exists():
            build_spatial_index([(index_slug, "hazards", geojson)], index)
        item = (result.as_dict() if result else {"source": ";".join(str(p) for p in sources), "output": str(geojson), "feature_count": feature_count, "crs": "EPSG:4326", "bbox": None, "skipped": [], "source_hash": "", "output_hash": ""}) | {
            "slug": slug,
            "category": "hazards",
            "name": f"{key[0]} 小時／{key[1]} 毫米淹水範圍",
            "format": "spatial",
            "output": str(index),
            "source_count": len(sources),
            "spatial": {"indexed": True, "index_path": str(index), "full_geometry_source": str(geojson)},
        }
        results[slug] = item
        print(f"{slug}: sources={len(sources)} features={item['feature_count']}")
    # Keep the historic API slug for the existing 24h/650 layer.
    legacy = results.pop("flood_24h_650mm")
    legacy["slug"] = "flood_650mm_24h"
    results["flood_650mm_24h"] = legacy
    catalog.pop("flood_24h_650mm", None)
    for slug, item in results.items():
        catalog[slug] = item
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"scenarios={len(results)}")


if __name__ == "__main__":
    main()
