"""Spatial validation and fixed search context for geology screening."""

from dataclasses import dataclass
from math import isfinite

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely.errors import GEOSException
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union


METRIC_CRS = CRS.from_epsg(3826)
_TO_METRES = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
_TO_LONLAT = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
WGS84_NAMES = {
    "epsg:4326", "urn:ogc:def:crs:epsg::4326",
    "ogc:crs84", "crs84", "urn:ogc:def:crs:ogc:1.3:crs84",
    "http://www.opengis.net/def/crs/ogc/1.3/crs84",
}
GEOMETRY_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
POLYGON_TYPES = {"Polygon", "MultiPolygon"}
MAX_BBOX_SPAN = 1.5
# Covers numeric noise at the inclusive projected boundary after a GeoJSON round trip.
DISTANCE_EPSILON_M = 1e-6
METHOD_VERSION = "worksite-screening-v1"


class DataValidationError(ValueError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def validate_wgs84(obj):
    if "crs" not in obj:
        return
    crs = obj["crs"]
    properties = crs.get("properties") if isinstance(crs, dict) else None
    name = properties.get("name") if isinstance(properties, dict) else None
    if not isinstance(name, str) or name.strip().casefold() not in WGS84_NAMES:
        raise DataValidationError("unsupported_crs", "Only WGS84 longitude/latitude GeoJSON is supported")


def _validate_positions(coordinates):
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        raise DataValidationError("invalid_geometry", "Geometry has empty or malformed coordinates")
    if isinstance(coordinates[0], (list, tuple)):
        for item in coordinates:
            _validate_positions(item)
        return
    if len(coordinates) not in (2, 3) or not all(type(v) in (int, float) and isfinite(v) for v in coordinates):
        raise DataValidationError("invalid_geometry", "Coordinates must be finite numeric positions")
    if not (-180 <= coordinates[0] <= 180 and -90 <= coordinates[1] <= 90):
        raise DataValidationError("invalid_crs", "Coordinates exceed WGS84 longitude/latitude bounds")


def wgs84_shape(geometry, *, types=GEOMETRY_TYPES):
    if not isinstance(geometry, dict) or geometry.get("type") not in types:
        raise DataValidationError("invalid_geometry", "Unsupported geometry type")
    validate_wgs84(geometry)
    _validate_positions(geometry.get("coordinates"))
    try:
        value = shape(geometry)
    except (ValueError, TypeError, IndexError, AttributeError, GEOSException) as exc:
        raise DataValidationError("invalid_geometry", "Invalid GeoJSON geometry") from exc
    if value.is_empty or not value.is_valid:
        raise DataValidationError("invalid_geometry", "Geometry must be valid and nonempty")
    return value


def project_geometry(geometry):
    """Project a geometry in the supported regional domain; reject failed transforms."""
    west, south, east, north = METRIC_CRS.area_of_use.bounds
    min_x, min_y, max_x, max_y = geometry.bounds
    if not (west <= min_x <= max_x <= east and south <= min_y <= max_y <= north):
        raise DataValidationError("invalid_crs", "Geometry lies outside the EPSG:3826 screening domain")
    try:
        result = transform(_TO_METRES.transform, geometry)
    except (ProjError, ValueError, GEOSException) as exc:
        raise DataValidationError("invalid_crs", "Geometry projection failed") from exc
    if not all(isfinite(value) for value in result.bounds) or not result.is_valid:
        raise DataValidationError("invalid_crs", "Projection returned invalid geometry")
    return result


def parse_buffer(value):
    try:
        radius = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("buffer_m must be a finite non-negative number") from exc
    if not isfinite(radius) or radius < 0:
        raise ValueError("buffer_m must be a finite non-negative number")
    return radius if radius else 0.0


@dataclass(frozen=True)
class ScreeningContext:
    worksite: object
    metadata: dict

    @property
    def bbox(self):
        bounds = self.metadata.get("query_bbox")
        return ",".join(map(str, bounds)) if bounds else None


def fixed_screening_context(project, source, geometry_hash, radius):
    radius = parse_buffer(radius)
    metadata = {
        "mode": "screening", "project_id": project.pk, "plan_id": None,
        "revision_id": None, "published_version": None, "geometry_hash": None,
        "geometry": None, "buffer_geometry": None, "buffer_m": radius, "buffer_unit": "m",
        "query_bbox": None, "query_crs": "OGC:CRS84", "projection": "EPSG:3826",
        "method_version": METHOD_VERSION, "publication_status": None,
    }
    if source is None:
        return ScreeningContext(None, metadata)

    validate_wgs84(source.geometry)
    polygons = [wgs84_shape(f["geometry"], types=POLYGON_TYPES) for f in source.geometry["features"]]
    worksite = project_geometry(unary_union(polygons))
    if worksite.area <= 0:
        raise DataValidationError("invalid_geometry", "Published worksite has zero area")
    x0, y0, x1, y1 = worksite.bounds
    # A request this wide cannot fit the existing 1.5-degree upstream envelope.
    if radius > 200_000:
        raise ValueError("screening range exceeds the supported bbox size")
    radius_pad = radius + DISTANCE_EPSILON_M
    bounds = _TO_LONLAT.transform_bounds(
        x0 - radius_pad, y0 - radius_pad, x1 + radius_pad, y1 + radius_pad, densify_pts=41,
    )
    # The sample-based inverse bounds receive an outward margin to avoid rounding out edge hits.
    pad = 1e-7
    west, south, east, north = (bounds[0]-pad, bounds[1]-pad, bounds[2]+pad, bounds[3]+pad)
    a, b, c, d = METRIC_CRS.area_of_use.bounds
    if not all(map(isfinite, bounds)) or not (a <= west < east <= c and b <= south < north <= d):
        raise ValueError("screening range exceeds the EPSG:3826 domain")
    if east-west > MAX_BBOX_SPAN or north-south > MAX_BBOX_SPAN:
        raise ValueError("screening range exceeds the supported bbox size")
    metadata.update(
        plan_id=source.plan_id, revision_id=source.revision.pk if source.revision else None,
        published_version=source.version, geometry_hash=geometry_hash,
        geometry=source.geometry, publication_status=source.status,
        query_bbox=[west, south, east, north],
        buffer_geometry=mapping(transform(_TO_LONLAT.transform, worksite.buffer(radius))) if radius else None,
    )
    return ScreeningContext(worksite, metadata)
