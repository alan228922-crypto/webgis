from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd


@dataclass
class ValidationResult:
    ok: bool
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    feature_count: int = 0
    crs: str | None = None
    bbox: list[float] | None = None


def validate_output(path: Path, expected_kind: str) -> ValidationResult:
    path = Path(path)
    result = ValidationResult(ok=True, path=str(path))
    if not path.exists() or path.stat().st_size == 0:
        result.ok = False
        result.errors.append("output file is missing or empty")
        return result
    try:
        if expected_kind == "vector":
            gdf = gpd.read_file(path)
            result.feature_count = len(gdf)
            result.crs = gdf.crs.to_string() if gdf.crs else None
            result.bbox = gdf.total_bounds.tolist() if not gdf.empty else None
            if result.crs != "EPSG:4326":
                result.ok = False
                result.errors.append("vector CRS must be EPSG:4326")
            if gdf.empty:
                result.ok = False
                result.errors.append("vector contains no features")
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            result.feature_count = len(payload.get("records", payload if isinstance(payload, list) else []))
    except Exception as exc:
        result.ok = False
        result.errors.append(f"cannot read output: {exc}")
    return result
