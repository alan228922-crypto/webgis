import json
from types import SimpleNamespace

import httpx
import pytest
from django.core.cache import cache
from pyproj import Geod, Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping

from worksite_gis.data_pipeline.spatial_index import build_spatial_index, query_bbox_by_source_category
from worksite_gis.integrations.geology_cloud import GEOLOGY_LAYERS, GeologyCloudAdapter, GeologyDatasetEvidence, normalize_geojson
from worksite_gis.models import Plan, Project
from worksite_gis.planning import validate_feature_collection
from worksite_gis.services.geology_geometry import fixed_screening_context
from worksite_gis.services.geology_screening import _fixed_candidates
from worksite_gis.services.worksite_publication import PublicWorksite
from worksite_gis.tests.test_phase3_screening import (
    FIXTURES, TIME, area_settings, collection, published,
)


BASE = "/api/projects/synthetic/geology/"
EVIDENCE = GeologyDatasetEvidence("synthetic", "fixture-v1", "2024-12-20", complete=True)


@pytest.mark.django_db
def test_three_valid_viewports_leave_context_unchanged(client, published, settings, monkeypatch):
    project, revision = published
    area_settings(settings, project, revision)
    seen = []

    def fetch(adapter, query):
        seen.append((adapter.layer, query))
        return FIXTURES["layers"][adapter.layer], TIME, EVIDENCE

    monkeypatch.setattr(GeologyCloudAdapter, "fetch", fetch)
    # Constant synthetic site occupies lon 121–121.001 and lat 24.99298–24.99389.
    windows = ["120.99,24.98,121.02,25.01", "121.0005,24.98,121.02,25.01", "121.04,24.98,121.06,25.01"]
    responses = [client.get(BASE+"summary/", {"mode": "screening", "buffer_m":50, "bbox":w}).json() for w in windows]
    first = responses[0]
    assert len(seen) == 5
    for response in responses:
        assert response["screening_context"] == first["screening_context"]
        assert [l["screening"] for l in response["layers"]] == [l["screening"] for l in first["layers"]]
        assert response["screening_context"]["published_version"] == 1
    assert first["screening_context"]["geometry"] == revision.geometry
    for viewport in windows:
        explicit = client.get(BASE+"drill/", {"mode":"exploration","bbox":viewport}).json()
        legacy = client.get(BASE+"drill/", {"bbox":viewport}).json()
        assert explicit["screening_context"]["query_bbox"] == list(map(float,viewport.split(",")))
        assert explicit["features"] == legacy["features"]
        assert "screening_context" not in legacy
    assert len({query["bbox"] for layer,query in seen if "bbox" in query}) == 4
    for invalid in (-1, "x", "NaN", "Infinity", 190000):
        rejected = client.get(BASE+"drill/", {"mode":"screening","buffer_m":invalid})
        assert rejected.status_code == 400
        assert rejected.json()["data_quality"]["failure_reason"] == "invalid_screening_parameters"


@pytest.mark.django_db
def test_overlap_order_duplicate_and_wrong_area_evidence(client, published, settings, monkeypatch):
    project, revision = published
    area_settings(settings, project, revision)
    features = FIXTURES["layers"]["liquefaction"]["features"]
    params = {"mode":"screening","buffer_m":50,"limit":1}
    url = BASE+"liquefaction/"
    for variant in (features, list(reversed(features)), [*features,features[0],features[1]]):
        cache.clear()
        monkeypatch.setattr(GeologyCloudAdapter,"fetch",
                            lambda self,query: ({"type":"FeatureCollection","features":variant},TIME,EVIDENCE))
        result = client.get(url,params).json()
        assert len(result["features"]) == 1
        assert result["screening"]["intersection_area_m2"] == pytest.approx(5000+2500-1250,abs=.02)
        assert result["screening"]["intersection_ratio"] == pytest.approx(.625,abs=1e-6)
    certificate = settings.WORKSITE_GEOLOGY_LIQUEFACTION_AREAS[str(revision.pk)]
    certificate["coverage_bbox"] = [121.02,24.98,121.1,25.05]
    result = client.get(url,params).json()
    assert result["screening"]["intersection_ratio"] is None
    assert result["data_quality"]["failure_reason"] == "administrative_coverage_unknown"
    certificate["geometry_hash"]="different_revision"
    assert client.get(url,params).json()["data_quality"]["failure_reason"] == "unverified_administrative_area"


@pytest.mark.parametrize("layer", sorted(GEOLOGY_LAYERS))
@pytest.mark.django_db
def test_layer_quality_axes_remain_separate(client, published, settings, monkeypatch, layer):
    project, revision = published
    area_settings(settings, project, revision)
    url = BASE+layer+"/"
    features = FIXTURES["layers"][layer]
    params = {"mode":"screening","buffer_m":50}
    scenarios = (
        ("live",features,None,("ok","live",None)),
        ("empty",collection(),None,("empty","live",None)),
        ("network",None,httpx.ConnectError("synthetic connection"),("error",None,"network_failure")),
        ("timeout",None,TimeoutError("synthetic timeout"),("error",None,"timeout")),
        ("json",{"type":"invalid"},None,("error",None,"invalid_response")),
        ("crs",dict(features,crs={"properties":{"name":"EPSG:3826"}}),None,("error",None,"unsupported_crs")),
        ("invalid",{"type":"FeatureCollection","features":[{"type":"Feature","geometry":mapping(Point()),"properties":{}}]},None,("error",None,"invalid_geometry")),
    )
    for name,payload,error,expected in scenarios:
        cache.clear()
        def provider(adapter, query):
            if error: raise error
            return payload,TIME
        monkeypatch.setattr(GeologyCloudAdapter,"fetch",provider)
        row = client.get(url,params).json()
        q = row["data_quality"]
        assert (q["retrieval"],q["cache"],q["failure_reason"]) == expected, (name,layer)
        assert not q["completeness"]["analysis_input_complete"]
        assert q["completeness"]["upstream_limit_unknown"] and q["completeness"]["upstream_coverage_unknown"]
        assert q["source_published_at"] is None and q["generated_at"] != q["source_published_at"]
    cache.clear()
    monkeypatch.setattr(GeologyCloudAdapter,"fetch",lambda a,q:(features,TIME,EVIDENCE))
    live = client.get(url,params).json()["data_quality"]
    fresh = client.get(url,params).json()["data_quality"]
    assert fresh["cache"] == "fresh_cache"
    assert live["source_published_at"] == "2024-12-20" and live["fetched_at"] == TIME.isoformat()
    read = cache.get
    monkeypatch.setattr(cache,"get",lambda key,*a,**kw: None if key.startswith("geology-layer:") and not key.endswith(":stale") else read(key,*a,**kw))
    def timeout(*args):
        raise TimeoutError("synthetic timeout")
    monkeypatch.setattr(GeologyCloudAdapter,"fetch",timeout)
    stale = client.get(url,params).json()["data_quality"]
    assert stale["cache"] == "stale_cache" and stale["failure_reason"] == "timeout"
    assert stale["fetched_at"] == fresh["fetched_at"] == live["fetched_at"]


@pytest.mark.django_db
def test_legacy_without_selected_worksite(client, published, monkeypatch):
    project, _ = published
    Project.objects.filter(pk=project.pk).update(geology_source_plan=None)
    params = {"mode":"screening","buffer_m":50}
    url = BASE+"drill/"
    assert client.get(url,params).json()["data_quality"]["failure_reason"] == "no_worksite_geometry"
    legacy = Plan.objects.create(project=project,status="approved",name="legacy fixture",geometry=FIXTURES["worksite"])
    monkeypatch.setattr(GeologyCloudAdapter,"fetch",lambda a,q:(FIXTURES["layers"]["drill"],TIME))
    row = client.get(url,params).json()
    assert row["screening_context"]["plan_id"] == legacy.pk
    assert row["screening_context"]["publication_status"] == "legacy_unverified"


def test_multipolygon_distance_and_equal_distance_order():
    multi = MultiPolygon([box(0,0,100,100),box(200,0,300,100)])
    source = PublicWorksite(1,1,collection(multi),TIME,None,"verified")
    analysis = fixed_screening_context(SimpleNamespace(pk=1),source,"synthetic",50)
    inputs = collection(Point(180,50),Point(180,50),LineString([(-5,50),(0,50)]))
    ids = ["Z","A","edge"]
    for feature, stable_id in zip(inputs["features"], ids):
        feature["id"] = stable_id
    records, screening = _fixed_candidates(inputs,analysis,"drill")
    assert [f[2]["id"] for f in records] == ["edge","A","Z"]
    assert [f[0] for f in records] == pytest.approx([0,20,20],abs=1e-6)
    assert screening["intersects"] and screening["nearest_distance_m"] == 0


def test_layer_positions_inverse_and_ellipsoid_area():
    forward = Transformer.from_crs(4326,3826,always_xy=True)
    inverse = Transformer.from_crs(3826,4326,always_xy=True)
    for lon,lat in [(121,25),(120,23),(122,24)]:
        assert inverse.transform(*forward.transform(lon,lat)) == pytest.approx((lon,lat),abs=1e-9)
    xs, ys = [121,121.01,121.01,121,121], [25,25,25.01,25.01,25]
    fc = {"type":"FeatureCollection","features":[{"type":"Feature","geometry":mapping(Polygon(zip(xs,ys))),"properties":{}}]}
    expected = abs(Geod(ellps="WGS84").polygon_area_perimeter(xs,ys)[0])
    actual = validate_feature_collection(fc,"EPSG:4326")["geometry"]["features"][0]["properties"]["area_m2"]
    assert actual == pytest.approx(expected,abs=.01)
    fc["features"][0]["geometry"] = mapping(Polygon([(121,25),(121.01,25.01),(121,25.01),(121.01,25),(121,25)]))
    with pytest.raises(ValueError):
        normalize_geojson(fc)


def test_rtree_complete_empty_is_distinct_from_exhausted_empty(tmp_path):
    outer = box(121,25,121.04,25.04)
    hole = box(121.005,25.005,121.035,25.035)
    donut = Polygon(outer.exterior.coords,[hole.exterior.coords])
    features = [{"type":"Feature","properties":{"source_category":category},"geometry":mapping(geom)}
                for category,geom in (("L",donut),("L",donut),("G",Point(121.02,25.02)))]
    source = tmp_path/"synthetic.geojson"
    source.write_text(json.dumps({"type":"FeatureCollection","features":features}))
    index = tmp_path/"synthetic.sqlite3"
    build_spatial_index([("synthetic","hazards",source)],index)
    q = (121.015,25.015,121.025,25.025)
    for budget, expected_complete in [(1,False),(2,True)]:
        matches,quality = query_bbox_by_source_category(index,"synthetic",q,"L",limit=1,scan_budget=budget,with_quality=True)
        assert matches == [] and quality["scan_complete"] is expected_complete
        assert quality["scan_incomplete"] is not expected_complete
        assert not quality["display_truncated"]


@pytest.mark.django_db
def test_distinct_radii_isolate_cache_even_with_identical_bbox(published):
    from dataclasses import replace

    from worksite_gis.services.geology_screening import GeologyCloudService, worksite_geometry_hash
    from worksite_gis.services.worksite_publication import geology_source

    project, revision = published
    project.refresh_from_db()
    publication = geology_source(project)
    first = fixed_screening_context(project, publication[0], worksite_geometry_hash(revision.geometry), 50)
    # Identical upstream bbox isolates the radius from all other cache-key inputs.
    second = replace(first, metadata={**first.metadata, "buffer_m": 51})
    service = GeologyCloudService()
    keys = [service.prepare_cache_context("drill", bbox=first.bbox, project=project,
        limit=100, publication=publication, analysis=analysis)["cache_key"] for analysis in (first,second)]
    assert keys[0] != keys[1]
