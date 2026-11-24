from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_catalog(results: list[dict[str, Any]], catalog_dir: Path) -> dict[str, Any]:
    catalog_dir = Path(catalog_dir)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    layers = {item["slug"]: item for item in results}
    imports = [{**item, "output_hash": _hash(Path(item["output"])) if Path(item["output"]).exists() else ""} for item in results]
    validation = {item["slug"]: item.get("validation", {}) for item in results}
    for name, payload in (("layers.json", layers), ("imports.json", imports), ("validation_report.json", validation)):
        target = catalog_dir / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    return layers


def build_offline_tiles_manifest(tile_root: Path, output: Path) -> dict[str, Any]:
    tiles = [p for p in Path(tile_root).rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    manifest = {"root": str(tile_root), "file_count": len(tiles), "extensions": sorted({p.suffix.lower() for p in tiles})}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
