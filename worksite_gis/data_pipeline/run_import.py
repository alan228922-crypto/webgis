from __future__ import annotations

import json
from pathlib import Path

from .catalog import build_catalog, build_offline_tiles_manifest
from .convert import convert_excel_workbook, convert_geojson, convert_table, convert_vector, convert_vector_collection
from .sources import discover_vector_sources
from .validate import validate_output


API_WORKBOOK_SPECS = [
    ("weather_stations", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "\u6c23\u8c61\u7ad9\u4f4d\u7f6e.xlsx"), "api_seed", "weather_stations"),
    ("rail_station_codes", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "\u53f0\u9435\u8eca\u7ad9\u7de8\u865f.xlsx"), "api_seed", "rail_station_codes"),
    ("station_codes", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "\u8eca\u7ad9\u8207\u7de8\u865f.xlsx"), "api_seed", "station_codes"),
    ("water_level_stations", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "\u6c34\u4f4d", "\u6cb3\u5ddd\u6c34\u4f4d\u6e2c\u7ad9\u7ad9\u6cc1.xlsx"), "api_seed", "water_level_stations"),
    ("cctv_stations", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "CCTV", "Taiwan_Real_CCTV_Full.xlsx"), "api_seed", "cctv_stations"),
    ("twipcam_cctv_catalog", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "CCTV", "twipcam_\u53f0\u7063CCTV\u5927\u6e05\u55ae_2026-08-04.xlsx"), "api_seed", "twipcam_cctv_catalog"),
    ("source_inventory", ("\u0061\u0070\u0069\u8cc7\u6599\u8868", "\u8cc7\u6599\u5eab Inventory.xlsx"), "reference", "source_inventory"),
]


def convert_api_workbooks(source_root: Path, output_root: Path) -> list[dict]:
    source_root = Path(source_root)
    output_root = Path(output_root)
    results: list[dict] = []
    for slug, relative_parts, category, name in API_WORKBOOK_SPECS:
        source = source_root.joinpath(*relative_parts)
        if not source.exists():
            continue
        output_group = "references" if category == "reference" else "api_seed"
        csv_output = output_root / "processed" / "tables_csv" / f"{slug}.csv"
        result = convert_excel_workbook(source, output_root / "processed" / output_group / f"{slug}.json", slug, csv_output=csv_output)
        item = result.as_dict() | {
            "slug": slug,
            "category": category,
            "name": name,
            "format": "json",
            "csv_output": str(csv_output),
        }
        item["validation"] = validate_output(Path(result.output), "table").__dict__
        results.append(item)
    return results


def _catalog_item(result, slug: str, category: str, name: str | None = None, fmt: str = "geojson", **metadata) -> dict:
    item = result.as_dict() | {"slug": slug, "category": category, "name": name or slug, "format": fmt, **metadata}
    item["validation"] = validate_output(Path(result.output), "vector" if fmt == "geojson" else "table").__dict__
    return item


def run_import(source_root: Path, output_root: Path, tile_root: Path) -> dict:
    source_root = Path(source_root)
    output_root = Path(output_root)
    results: list[dict] = []

    transport_out = output_root / "processed" / "gis" / "transport"
    for slug, source in discover_vector_sources(source_root).items():
        result = convert_vector(source, transport_out / f"{slug}.geojson", slug)
        results.append(_catalog_item(result, slug, "transport"))

    gis_root = next((p for p in source_root.iterdir() if p.is_dir() and "GIS" in p.name), source_root / "\u0047\u0049\u0053\u5716\u8cc7")
    candidates = {
        "township": next(gis_root.rglob("TOWN_MOI_*.shp"), None) if gis_root.exists() else None,
        "debris_flow": next(gis_root.rglob("debrisstream*.shp"), None) if gis_root.exists() else None,
        "flood_650mm_24h": next((p for p in gis_root.rglob("*24h650*.shp") if p.with_suffix(".prj").exists()), None) if gis_root.exists() else None,
    }
    for slug, source in candidates.items():
        if source is None:
            continue
        category = "boundary" if slug == "township" else "hazards"
        result = convert_vector(source, output_root / "processed" / "gis" / category / f"{slug}.geojson", slug)
        results.append(_catalog_item(result, slug, category))

    if gis_root.exists():
        protection = next((p for p in gis_root.rglob("*.shp") if "2024" in p.name and p.with_suffix(".prj").exists()), None)
        if protection is not None:
            result = convert_vector(protection, output_root / "processed" / "gis" / "protection" / "nature_reserves.geojson", "nature_reserves")
            results.append(_catalog_item(result, "nature_reserves", "protection"))

        sensitive = next(gis_root.rglob("response_*.json"), None)
        if sensitive is not None:
            result = convert_geojson(sensitive, output_root / "processed" / "gis" / "hazards" / "geologic_sensitive_areas.geojson", "geologic_sensitive_areas")
            results.append(_catalog_item(result, "geologic_sensitive_areas", "hazards"))

        flood_root = next((p for p in gis_root.iterdir() if p.is_dir() and p.name.startswith("7_")), None)
        if flood_root is not None:
            flood_sources = [p for p in flood_root.rglob("*.shp") if "650" in p.stem.lower() and "24" in p.stem.lower() and p.with_suffix(".prj").exists()]
            if flood_sources:
                result = convert_vector_collection(flood_sources, output_root / "processed" / "gis" / "hazards" / "flood_650mm_24h.geojson", "flood_650mm_24h")
                results.append(_catalog_item(result, "flood_650mm_24h", "hazards", source_count=len(flood_sources)))

        soil_root = next((p for p in gis_root.iterdir() if p.is_dir() and p.name.startswith("8_")), None)
        if soil_root is not None:
            soil_index = next(soil_root.glob("*.csv"), None)
            if soil_index is not None:
                result = convert_table(soil_index, output_root / "processed" / "references" / "soil_liquefaction_sources.json", "soil_liquefaction_sources")
                results.append(_catalog_item(result, "soil_liquefaction_sources", "reference", fmt="json"))

    results.extend(convert_api_workbooks(source_root, output_root))

    previous_catalog = output_root / "catalog" / "layers.json"
    if previous_catalog.exists():
        try:
            previous = json.loads(previous_catalog.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
        current_slugs = {item["slug"] for item in results}
        for slug, item in previous.items():
            if slug in current_slugs:
                continue
            if Path(item.get("output", "")).exists():
                results.append(item)

    transport_dir = output_root / "processed" / "gis" / "transport"
    for slug in ("tra_stations", "rail_lines", "mrt_stations", "mrt_lines", "highways"):
        output = transport_dir / f"{slug}.geojson"
        if output.exists() and not any(item["slug"] == slug for item in results):
            report = validate_output(output, "vector")
            results.append({
                "slug": slug, "category": "transport", "name": slug, "format": "geojson",
                "source": "cleaned-after-conversion", "output": str(output),
                "feature_count": report.feature_count, "crs": report.crs, "bbox": report.bbox,
                "skipped": [], "source_hash": "", "output_hash": "", "validation": report.__dict__,
            })

    build_catalog(results, output_root / "catalog")
    build_offline_tiles_manifest(tile_root, output_root / "processed" / "basemap" / "offline_tiles_manifest.json")
    return {"layers": results, "failed": [item for item in results if not item["validation"]["ok"]]}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, default=Path.cwd() / "data")
    parser.add_argument("--tile-root", type=Path, default=Path.cwd() / "\u5730\u5716\u5716\u78da")
    args = parser.parse_args()
    report = run_import(args.source_root, args.output_root, args.tile_root)
    print(json.dumps({"layers": len(report["layers"]), "failed": len(report["failed"])}, ensure_ascii=False))
    raise SystemExit(1 if report["failed"] else 0)
