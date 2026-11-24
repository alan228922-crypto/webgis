from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd


@dataclass
class ConversionResult:
    source: str
    output: str
    feature_count: int
    crs: str | None
    bbox: list[float] | None
    skipped: list[dict[str, Any]]
    source_hash: str = ""
    output_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_geometry(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("source vector has no CRS; refusing to infer silently")
    gdf = gdf.to_crs("EPSG:4326").copy()
    skipped: list[int] = []
    for index, geometry in gdf.geometry.items():
        if geometry is None or geometry.is_empty:
            skipped.append(index)
            continue
        if not geometry.is_valid:
            repaired = geometry.buffer(0)
            if repaired.is_empty or not repaired.is_valid:
                skipped.append(index)
            else:
                gdf.at[index, "geometry"] = repaired
    if skipped:
        gdf = gdf.drop(index=skipped)
    return gdf


def convert_vector(source: Path, output: Path, layer_slug: str) -> ConversionResult:
    source = Path(source)
    output = Path(output)
    gdf = normalize_geometry(gpd.read_file(source))
    output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(output, driver="GeoJSON")
    bounds = gdf.total_bounds.tolist() if not gdf.empty else None
    return ConversionResult(
        source=str(source),
        output=str(output),
        feature_count=len(gdf),
        crs="EPSG:4326",
        bbox=bounds,
        skipped=[],
        source_hash=_sha256(source),
        output_hash=_sha256(output),
    )


def convert_vector_collection(
    sources: list[Path], output: Path, layer_slug: str, *, missing_crs: str | None = None
) -> ConversionResult:
    """Merge multiple vector sources into one normalized GeoJSON layer."""
    if not sources:
        raise ValueError("vector collection is empty")
    frames = []
    for source in sources:
        frame = gpd.read_file(Path(source))
        if frame.crs is None and missing_crs:
            frame = frame.set_crs(missing_crs, allow_override=True)
        frames.append(normalize_geometry(frame))
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(output, driver="GeoJSON")
    bounds = gdf.total_bounds.tolist() if not gdf.empty else None
    digest = hashlib.sha256()
    for source in sources:
        digest.update(_sha256(Path(source)).encode())
    return ConversionResult(
        source=";".join(str(Path(source)) for source in sources),
        output=str(output),
        feature_count=len(gdf),
        crs="EPSG:4326",
        bbox=bounds,
        skipped=[],
        source_hash=digest.hexdigest(),
        output_hash=_sha256(output),
    )


def convert_geojson(source: Path, output: Path, layer_slug: str) -> ConversionResult:
    """Normalize an existing GeoJSON FeatureCollection into platform output."""
    return convert_vector(Path(source), Path(output), layer_slug)


def convert_table(source: Path, output: Path, kind: str) -> ConversionResult:
    source = Path(source)
    output = Path(output)
    if source.suffix.lower() in {".xlsx", ".xls"}:
        return convert_excel_workbook(source, output, kind)
    elif source.suffix.lower() == ".csv":
        frame = pd.read_csv(source)
    else:
        raise ValueError(f"unsupported table source: {source.suffix}")
    rows = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"kind": kind, "records": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return ConversionResult(
        source=str(source), output=str(output), feature_count=len(rows), crs=None,
        bbox=None, skipped=[], source_hash=_sha256(source), output_hash=_sha256(output)
    )


def convert_excel_workbook(source: Path, output: Path, kind: str, csv_output: Path | None = None) -> ConversionResult:
    source = Path(source)
    output = Path(output)
    frames = []
    for sheet_name in pd.ExcelFile(source).sheet_names:
        frame = pd.read_excel(source, sheet_name=sheet_name)
        frame = frame.dropna(how="all")
        if frame.empty:
            continue
        frame.columns = [str(column).strip() for column in frame.columns]
        frame["_sheet"] = sheet_name
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rows = json.loads(combined.to_json(orient="records", force_ascii=False, date_format="iso"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"kind": kind, "records": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    if csv_output is not None:
        csv_output = Path(csv_output)
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(csv_output, index=False, encoding="utf-8-sig")
    return ConversionResult(
        source=str(source), output=str(output), feature_count=len(rows), crs=None,
        bbox=None, skipped=[], source_hash=_sha256(source), output_hash=_sha256(output)
    )
