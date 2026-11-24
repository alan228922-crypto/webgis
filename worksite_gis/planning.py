from copy import deepcopy

from pyproj import CRS, Geod, Transformer
from pyproj.exceptions import ProjError
from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.ops import transform

from .services.geology_geometry import validate_wgs84, wgs84_shape


GEOMETRY_ERRORS = (ValueError, TypeError, AttributeError, IndexError, OverflowError, GEOSException, ProjError)

ALLOWED_GEOMETRIES = {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}


def validate_feature_collection(payload: dict, measurement_crs="EPSG:3826") -> dict:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("geometry must be a GeoJSON FeatureCollection")
    validate_wgs84(payload)
    features = payload.get("features")
    if not isinstance(features, list) or len(features) > 500:
        raise ValueError("features must be a list with at most 500 items")
    projected = None
    if measurement_crs:
        crs = CRS.from_user_input(measurement_crs)
        if crs.is_geographic and crs.equals(CRS.from_epsg(4326), ignore_axis_order=True):
            measurement_crs = ""
        elif not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]):
            raise ValueError("measurement CRS must use projected metres or WGS84 geodesic measurement")
        else:
            projected = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    geod = Geod(ellps="WGS84")
    result = deepcopy(payload)
    for index, feature in enumerate(result["features"]):
        geometry_payload = feature.get("geometry") if isinstance(feature, dict) else None
        if not geometry_payload or geometry_payload.get("type") not in ALLOWED_GEOMETRIES:
            raise ValueError(f"feature {index} has unsupported geometry")
        validate_wgs84(feature)
        geometry = wgs84_shape(geometry_payload)
        measured = transform(projected, geometry) if projected else geometry
        properties = feature.setdefault("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"feature {index} properties must be an object")
        if "LineString" in geometry.geom_type:
            properties["length_m"] = round(measured.length if projected else geod.geometry_length(geometry), 2)
        if "Polygon" in geometry.geom_type:
            properties["area_m2"] = round(measured.area if projected else abs(geod.geometry_area_perimeter(geometry)[0]), 2)
    return {"geometry": result, "feature_count": len(features), "measurement_crs": measurement_crs or "geodesic", "measurement_method": "projected" if projected else "geodesic"}


def publication_geometry_error(geometry, *, require_source=True):
    try:
        features = validate_feature_collection(geometry, measurement_crs="")["geometry"]["features"]
    except GEOMETRY_ERRORS:
        return "published plans require valid worksite geometry"
    if not features:
        return "published plans require traceable Polygon features"
    for feature in features:
        source = feature["properties"].get("source")
        if (
            feature.get("type") != "Feature"
            or feature["geometry"]["type"] not in {"Polygon", "MultiPolygon"}
            or (require_source and (not isinstance(source, str) or not source.strip()))
        ):
            return "published plans require traceable Polygon features"
        west, south, east, north = shape(feature["geometry"]).bounds
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            return "published worksite coordinates must be within WGS84 bounds"
    return None
