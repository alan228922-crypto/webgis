from types import SimpleNamespace
from math import cos, radians, sin, sqrt, tan

import pytest
from shapely.geometry import Point, box, mapping

from worksite_gis.integrations.geology_cloud import normalize_geojson
from worksite_gis.planning import validate_feature_collection


def feature(geometry):
    return {"type":"Feature","geometry":mapping(geometry),"properties":{}}


def collection(*features, **data):
    return {"type":"FeatureCollection","features":list(features),**data}


def test_explicit_crs_and_coordinates_checked():
    with pytest.raises(ValueError, match="WGS84"):
        normalize_geojson(collection(feature(Point(250000,2765778)), crs={"properties":{"name":"EPSG:3826"}}))
    with pytest.raises(ValueError, match="WGS84"):
        normalize_geojson(collection(feature(Point(25,121))))
    assert len(normalize_geojson(collection(feature(Point(121,25))))["features"]) == 1
    with pytest.raises(ValueError, match="geometry"):
        normalize_geojson(collection({"type":"Feature","geometry":{"type":"GeometryCollection","geometries":[]},"properties":{}}))
    with pytest.raises(ValueError, match="finite"):
        normalize_geojson(collection(feature(Point(float("nan"), 25))))


def test_geographic_plan_measurement_uses_geodesic_units():
    r=validate_feature_collection(collection(feature(box(121,25,121.01,25.01))),"EPSG:4326")
    assert r["measurement_method"] == "geodesic"
    assert r["geometry"]["features"][0]["properties"]["area_m2"] > 1_000_000
    with pytest.raises(ValueError, match="projected metres"):
        validate_feature_collection(collection(feature(box(121,25,121.01,25.01))),"EPSG:2227")


@pytest.mark.parametrize("lon,lat", [(121,25),(120,23),(122,24)])
def test_multiple_tm2_controls(lon, lat):
    # Independent low-order Transverse Mercator series on the GRS80 ellipsoid.
    from worksite_gis.services.geology_geometry import project_geometry

    a, flattening = 6378137, 1 / 298.257222101
    e = flattening * (2 - flattening)
    phi = radians(lat)
    n = a / sqrt(1 - e * sin(phi)**2)
    t, c = tan(phi)**2, e * cos(phi)**2 / (1 - e)
    q = cos(phi) * radians(lon - 121)
    meridian = a * ((1-e/4-3*e**2/64-5*e**3/256)*phi
                    - (3*e/8+3*e**2/32+45*e**3/1024)*sin(2*phi)
                    + (15*e**2/256+45*e**3/1024)*sin(4*phi)
                    - 35*e**3/3072*sin(6*phi))
    x = 250000 + .9999*n*(q+(1-t+c)*q**3/6)
    y = .9999*(meridian+n*tan(phi)*(q**2/2+(5-t+9*c+4*c*c)*q**4/24))
    point = project_geometry(Point(lon, lat))
    assert point.x == pytest.approx(x, abs=.03)
    assert point.y == pytest.approx(y, abs=.03)


def test_fixed_context_uses_radius_and_checks_extent():
    from worksite_gis.services.geology_geometry import fixed_screening_context, parse_buffer
    from worksite_gis.services.worksite_publication import PublicWorksite
    source=PublicWorksite(4,2,collection(feature(box(121,25,121.001,25.001))),None,SimpleNamespace(pk=8),"verified")
    project=SimpleNamespace(pk=6)
    small=fixed_screening_context(project,source,"abc",0)
    large=fixed_screening_context(project,source,"abc",50)
    assert small.metadata["revision_id"] == large.metadata["revision_id"] == 8
    assert small.metadata["query_bbox"][0] > large.metadata["query_bbox"][0]
    assert small.metadata["query_bbox"][2] < large.metadata["query_bbox"][2]
    assert large.metadata["buffer_m"] == 50
    with pytest.raises(ValueError):
        fixed_screening_context(project,source,"abc",190000)
    for value in (-1,"NaN","Inf"):
        with pytest.raises(ValueError):
            parse_buffer(value)
