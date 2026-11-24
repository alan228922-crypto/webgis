from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from shapely import wkb
from shapely.geometry import Point, box, mapping, shape

from .simplify import _simplify_valid


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE risk_features (
            id INTEGER PRIMARY KEY,
            layer_slug TEXT NOT NULL,
            category TEXT NOT NULL,
            feature_index INTEGER NOT NULL,
            geometry_wkb BLOB NOT NULL,
            display_geometry_wkb BLOB NOT NULL,
            properties_json TEXT NOT NULL
        );
        CREATE INDEX risk_features_category_idx ON risk_features(category);
        CREATE VIRTUAL TABLE risk_features_rtree USING rtree(
            id, min_x, max_x, min_y, max_y
        );
        """
    )


def build_spatial_index(layers: list[tuple[str, str, Path]], output: Path) -> dict:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    counts: dict[str, int] = {}
    total = 0
    try:
        connection = sqlite3.connect(temporary)
        try:
            _create_schema(connection)
            for slug, category, source in layers:
                payload = json.loads(Path(source).read_text(encoding="utf-8-sig"))
                features = payload.get("features", [])
                counts[slug] = 0
                for feature_index, feature in enumerate(features):
                    geometry_payload = feature.get("geometry")
                    if not geometry_payload:
                        raise ValueError(f"{slug} feature {feature_index} has no geometry")
                    geometry = shape(geometry_payload)
                    if not geometry.is_valid:
                        geometry = geometry.buffer(0)
                    if geometry.is_empty or not geometry.is_valid:
                        raise ValueError(f"{slug} feature {feature_index} has invalid geometry")
                    properties = feature.get("properties") or {}
                    display_geometry = _simplify_valid(geometry, 0.0005)
                    cursor = connection.execute(
                        """
                        INSERT INTO risk_features(
                            layer_slug, category, feature_index, geometry_wkb,
                            display_geometry_wkb, properties_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            slug,
                            category,
                            feature_index,
                            sqlite3.Binary(geometry.wkb),
                            sqlite3.Binary(display_geometry.wkb),
                            json.dumps(properties, ensure_ascii=False),
                        ),
                    )
                    min_x, min_y, max_x, max_y = geometry.bounds
                    connection.execute(
                        "INSERT INTO risk_features_rtree(id, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
                        (cursor.lastrowid, min_x, max_x, min_y, max_y),
                    )
                    counts[slug] += 1
                    total += 1
            connection.commit()
        finally:
            connection.close()
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"feature_count": total, "layers": counts}


def query_point(
    index_path: Path,
    longitude: float,
    latitude: float,
    categories: tuple[str, ...],
) -> list[dict]:
    if not categories:
        return []
    placeholders = ",".join("?" for _ in categories)
    sql = f"""
        SELECT feature.layer_slug, feature.category, feature.geometry_wkb, feature.properties_json
        FROM risk_features_rtree AS bounds
        JOIN risk_features AS feature ON feature.id = bounds.id
        WHERE bounds.min_x <= ? AND bounds.max_x >= ?
          AND bounds.min_y <= ? AND bounds.max_y >= ?
          AND feature.category IN ({placeholders})
        ORDER BY feature.layer_slug, feature.feature_index
    """
    parameters = (longitude, longitude, latitude, latitude, *categories)
    point = Point(longitude, latitude)
    matches = []
    with sqlite3.connect(Path(index_path)) as connection:
        for slug, category, geometry_wkb, properties_json in connection.execute(sql, parameters):
            if wkb.loads(geometry_wkb).intersects(point):
                matches.append({
                    "slug": slug,
                    "category": category,
                    "properties": json.loads(properties_json),
                })
    return matches


def query_bbox(
    index_path: Path,
    layer_slug: str,
    bbox: tuple[float, float, float, float],
    limit: int = 500,
    *, scan_budget: int = 5000, with_quality: bool = False,
):
    return _query_bbox_features(index_path, layer_slug, bbox, limit=limit, scan_budget=scan_budget, with_quality=with_quality)


def query_bbox_by_source_category(
    index_path: Path,
    layer_slug: str,
    bbox: tuple[float, float, float, float],
    category: str | None = None,
    limit: int = 500,
    *, scan_budget: int = 5000, with_quality: bool = False,
):
    return _query_bbox_features(
        index_path, layer_slug, bbox, category=category, limit=limit, scan_budget=scan_budget, with_quality=with_quality
    )


def _query_bbox_features(
    index_path: Path,
    layer_slug: str,
    bbox: tuple[float, float, float, float],
    category: str | None = None,
    limit: int = 500,
    *, scan_budget: int = 5000, with_quality: bool = False,
):
    min_x, min_y, max_x, max_y = bbox
    if min_x >= max_x or min_y >= max_y:
        raise ValueError("bbox bounds are invalid")
    limit = min(max(int(limit), 1), 1000)
    if scan_budget < 1:
        raise ValueError("scan budget must be positive")
    viewport = box(min_x, min_y, max_x, max_y)
    category_clause = ""
    category_parameters: tuple[str | None, ...] = ()
    if category is not None:
        with sqlite3.connect(Path(index_path)) as connection:
            try:
                connection.execute(
                    "SELECT json_extract('{\"source_category\":\"L\"}', '$.source_category')"
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise sqlite3.DatabaseError(
                    "SQLite JSON1 is required for source category filtering"
                ) from exc
        category_clause = (
            " AND json_extract(feature.properties_json, "
            "'$.source_category') = ?"
        )
        category_parameters = (category,)
    sql = f"""
        SELECT feature.feature_index, feature.geometry_wkb, feature.display_geometry_wkb, feature.properties_json
        FROM risk_features_rtree AS bounds
        JOIN risk_features AS feature ON feature.id = bounds.id
        WHERE bounds.max_x >= ? AND bounds.min_x <= ?
          AND bounds.max_y >= ? AND bounds.min_y <= ?
          AND feature.layer_slug = ?
          {category_clause}
        ORDER BY feature.feature_index, feature.id
        LIMIT ?
    """
    features = []
    scanned = matches = 0
    scan_incomplete = False
    with sqlite3.connect(Path(index_path)) as connection:
        rows = connection.execute(
            sql, (min_x, max_x, min_y, max_y, layer_slug, *category_parameters, scan_budget + 1)
        )
        for feature_index, geometry_wkb, display_wkb, properties_json in rows:
            if scanned == scan_budget:
                scan_incomplete = True
                break
            scanned += 1
            geometry = wkb.loads(geometry_wkb)
            if not geometry.intersects(viewport):
                continue
            matches += 1
            if len(features) == limit:
                continue
            clipped = wkb.loads(display_wkb).intersection(viewport)
            if clipped.is_empty:
                clipped = geometry.intersection(viewport)
            if clipped.is_empty:
                continue
            features.append({
                "type": "Feature",
                "id": feature_index,
                "properties": json.loads(properties_json),
                "geometry": mapping(clipped),
            })
    quality = {
        "candidate_count_scanned": scanned,
        "exact_match_count": matches,
        "returned_count": len(features),
        "scan_budget": scan_budget,
        "scan_complete": not scan_incomplete,
        "scan_incomplete": scan_incomplete,
        "display_truncated": matches > limit,
    }
    return (features, quality) if with_quality else features
