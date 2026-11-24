from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from shapely.geometry import mapping, shape

from .convert import ConversionResult


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _simplify_valid(geometry, tolerance: float):
    current_tolerance = tolerance
    for _ in range(8):
        candidate = geometry.simplify(current_tolerance, preserve_topology=True)
        if not candidate.is_empty and candidate.is_valid:
            return candidate
        current_tolerance /= 2
    return geometry


def simplify_geojson(
    source: Path,
    output: Path,
    tolerance: float = 0.0005,
) -> ConversionResult:
    source = Path(source)
    output = Path(output)
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    simplified_features = []
    bounds = []
    for index, feature in enumerate(payload.get("features", [])):
        geometry_payload = feature.get("geometry")
        if not geometry_payload:
            raise ValueError(f"feature {index} has no geometry")
        geometry = shape(geometry_payload)
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        geometry = _simplify_valid(geometry, tolerance)
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"feature {index} cannot be simplified safely")
        simplified_feature = {
            "type": "Feature",
            "properties": feature.get("properties") or {},
            "geometry": mapping(geometry),
        }
        if "id" in feature:
            simplified_feature["id"] = feature["id"]
        simplified_features.append(simplified_feature)
        bounds.append(geometry.bounds)
    if not simplified_features:
        raise ValueError("source contains no features")
    result_payload = {
        "type": "FeatureCollection",
        "name": payload.get("name", source.stem),
        "features": simplified_features,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(result_payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    bbox = [
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    ]
    return ConversionResult(
        source=str(source),
        output=str(output),
        feature_count=len(simplified_features),
        crs="EPSG:4326",
        bbox=bbox,
        skipped=[],
        source_hash=_sha256(source),
        output_hash=_sha256(output),
    )
