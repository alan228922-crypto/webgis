import json
from shapely.geometry import Point, Polygon, LineString, box, mapping

from worksite_gis.data_pipeline.spatial_index import build_spatial_index, query_bbox, query_bbox_by_source_category

def test_rtree_uses_original_geometry_before_display_limit(tmp_path):
    window = (121.008, 24.998, 121.012, 25.002)
    donut = Polygon(box(121, 24.99, 121.02, 25.01).exterior.coords,
                    [box(121.003, 24.993, 121.017, 25.007).exterior.coords])
    geometries = [donut, donut, Point(121.01, 25), Point(121.012, 25),
                  LineString([(121,25), (121.001,25.0002), (121.002,25)])]
    source = tmp_path / "synthetic.geojson"
    source.write_text(json.dumps({"type":"FeatureCollection","features":[
        {"type":"Feature","geometry":mapping(g),"properties":{"source_category":"L"}}
        for g in geometries]}))
    index = tmp_path / "synthetic.sqlite3"
    build_spatial_index([("synthetic","hazards",source)],index)
    assert [f["id"] for f in query_bbox(index,"synthetic",window,1)] == [2]
    assert 4 in [f["id"] for f in query_bbox(index,"synthetic",(121.0009,25.00015,121.0011,25.00025))]
    hits, quality = query_bbox_by_source_category(index,"synthetic",window,"L",1,with_quality=True)
    assert [f["id"] for f in hits] == [2]
    assert quality["display_truncated"] and quality["scan_complete"]
    _, quality = query_bbox(index,"synthetic",window,1,scan_budget=2,with_quality=True)
    assert quality["scan_incomplete"] and not quality["display_truncated"]
