from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest
from django.core.cache import cache
from pyproj import Transformer
from shapely.geometry import LineString, Point, box, mapping
from shapely.ops import transform

from worksite_gis.integrations.geology_cloud import GeologyCloudAdapter
from worksite_gis.models import Plan, PlanVersion, PlanWorkflowEvent, Project
from worksite_gis.services.geology_screening import worksite_geometry_hash

CASE = Path(__file__).resolve().parents[2] / "docs/cases/synthetic-geological-screening"
MANIFEST = json.loads((CASE / "manifest.json").read_text())
FIXTURES = json.loads((CASE / "fixtures.json").read_text())
REFERENCE = json.loads((CASE / "expected.json").read_text())
ORIGIN = MANIFEST["origin_m"]
INVERSE = Transformer.from_crs(3826, 4326, always_xy=True)
TIME = datetime.fromisoformat(MANIFEST["mock_fetched_at"])


def geometry(value):
    return mapping(transform(lambda x,y: INVERSE.transform(x+ORIGIN[0], y+ORIGIN[1]), value))


def collection(*shapes):
    return {"type":"FeatureCollection","features":[
        {"type":"Feature","id":i,"geometry":geometry(g),"properties":{"source":"synthetic","county":"untrusted"}}
        for i,g in enumerate(shapes)]}


@pytest.fixture
def published(client, django_user_model):
    cache.clear()
    actor=django_user_model.objects.create_user(username="synthetic-screening")
    project=Project.objects.create(slug="synthetic",name="Synthetic")
    plan=Plan.objects.create(name="Current private v2",project=project,created_by=actor,version=2,geometry=collection(box(350,350,450,450)))
    snapshot=PlanVersion.objects.create(plan=plan,name="Synthetic published v1",version=1,status="draft",geometry=FIXTURES["worksite"])
    publication=PlanWorkflowEvent.objects.create(plan=plan,revision=snapshot,actor=actor,actor_label=actor.username,action="published")
    Plan.objects.filter(pk=plan.pk).update(publication_event=publication)
    Project.objects.filter(pk=project.pk).update(geology_source_plan=plan)
    client.force_login(actor)
    yield project, snapshot
    cache.clear()


def area_settings(settings, project, snapshot):
    settings.WORKSITE_GEOLOGY_LIQUEFACTION_AREAS = {
        str(snapshot.pk): {"project_id":project.pk, "geometry_hash":worksite_geometry_hash(snapshot.geometry),
                           "area":"synthetic-district","source":"synthetic coverage certificate",
                           "coverage_bbox":[120.9,24.8,121.1,25.1]},
    }


def layers():
    return FIXTURES["layers"]


def test_fixture_manifest_and_reference_worksite():
    from shapely.geometry import shape
    from worksite_gis.services.geology_geometry import project_geometry

    for name, checksum in MANIFEST["sha256"].items():
        assert hashlib.sha256((CASE / name).read_bytes()).hexdigest() == checksum
    site = project_geometry(shape(FIXTURES["worksite"]["features"][0]["geometry"]))
    assert site.area == pytest.approx(REFERENCE["worksite_area_m2"], abs=.02)
    for feature in FIXTURES["layers"]["drill"]["features"]:
        actual = site.distance(project_geometry(shape(feature["geometry"])))
        assert actual == pytest.approx(REFERENCE["drill"]["distance_m_by_id"][str(feature["id"])], abs=1e-6)


@pytest.mark.django_db
def test_screening_is_fixed_and_area_independent_of_display_limit(client, published, settings, monkeypatch):
    from worksite_gis.integrations.geology_cloud import GeologyDatasetEvidence
    project, snapshot = published
    area_settings(settings,project,snapshot)
    evidence=GeologyDatasetEvidence("synthetic","fixture-v1",None,complete=True)
    samples=layers()
    queried=[]
    def fetch(adapter, query):
        queried.append((adapter.layer,query))
        return samples[adapter.layer],TIME,evidence
    monkeypatch.setattr(GeologyCloudAdapter,"fetch",fetch)
    url="/api/projects/synthetic/geology/summary/"
    input_data={"mode":"screening","buffer_m":"50"}
    first=client.get(url,{**input_data,"bbox":"190,90,190,90","limit":1})
    second=client.get(url,{**input_data,"bbox":"120,24,120.2,24.2","limit":2})
    assert first.status_code == second.status_code == 200
    result=first.json()
    context=result["screening_context"]
    assert context == second.json()["screening_context"]
    assert context["published_version"] == 1 and context["revision_id"] == snapshot.pk
    assert context["buffer_m"] == 50 and context["projection"] == "EPSG:3826"
    rows={row["layer"]:row for row in result["layers"]}
    assert {k:r["screening"]["intersects"] for k,r in rows.items()} == REFERENCE["relations"]
    assert rows["rock_fall"]["screening"]["nearest_distance_m"] == REFERENCE["rock_fall_distance_m"]
    assert rows["drill"]["screening"]["nearby_feature_count"] == 3
    for payload in (result,second.json()):
        liquid=next(row for row in payload["layers"] if row["layer"]=="liquefaction")
        assert liquid["screening"]["intersection_area_m2"] == pytest.approx(REFERENCE["liquefaction"]["overlap_area_m2"],abs=.02)
        assert liquid["screening"]["intersection_ratio"] == pytest.approx(REFERENCE["liquefaction"]["overlap_ratio"],abs=1e-6)
        assert liquid["data_quality"]["completeness"]["display_truncated"]
        assert liquid["data_quality"]["source_published_at"] is None
        assert liquid["data_quality"]["dataset_version"] == "fixture-v1"
    assert all(q == {"area":"synthetic-district"} if layer=="liquefaction" else q["bbox"] for layer,q in queried)
    layerurl="/api/projects/synthetic/geology/drill/"
    drill = client.get(layerurl,input_data).json()
    assert [feature["id"] for feature in drill["features"]] == REFERENCE["drill"]["exact_ids"]
    zero=client.get(layerurl,{"mode":"screening"}).json()
    assert zero["screening_context"]["buffer_m"] == 0 and zero["screening"]["nearby_feature_count"] == 1
    assert client.get(url,{"mode":"screening","buffer_m":"Infinity"}).json()["data_quality"]["failure_reason"] == "invalid_screening_parameters"
    assert client.get(url,{"mode":"screening","buffer_m":200001}).status_code == 400


@pytest.mark.django_db
def test_unverified_coverage_and_area_fail_closed(client, published, settings, monkeypatch):
    project,snapshot=published
    samples=layers()
    queried=[]
    def fetch(adapter,query):
        queried.append(adapter.layer)
        return samples[adapter.layer],TIME
    monkeypatch.setattr(GeologyCloudAdapter,"fetch",fetch)
    summary="/api/projects/synthetic/geology/summary/"
    args={"mode":"screening","buffer_m":50}
    result=client.get(summary,args).json()
    by_layer={row["layer"]:row for row in result["layers"]}
    assert by_layer["drill"]["status"] == "live"
    assert by_layer["liquefaction"]["screening"]["intersection_ratio"] is None
    assert by_layer["liquefaction"]["data_quality"]["failure_reason"] == "missing_administrative_area"
    assert "liquefaction" not in queried
    area_settings(settings,project,snapshot)
    url="/api/projects/synthetic/geology/liquefaction/"
    row=client.get(url,args).json()
    assert row["screening"]["intersects"]
    assert row["screening"]["intersection_ratio"] is None
    assert row["screening"]["overlap_reason"] == "analysis_input_incomplete"
    assert row["data_quality"]["completeness"]["upstream_coverage_unknown"]


@pytest.mark.django_db
def test_formal_cache_separates_radius_mode_revision_and_archive(client, published, monkeypatch, settings):
    from worksite_gis.integrations.geology_cloud import GeologyDatasetEvidence
    from worksite_gis.services.worksite_publication import geology_source
    from worksite_gis.services.geology_geometry import fixed_screening_context
    from worksite_gis.services.geology_screening import GeologyCloudService

    project, revision = published
    url = "/api/projects/synthetic/geology/drill/"
    fixture = layers()["drill"]
    requested = []
    upstream = {"state": "ok"}

    def fetch(adapter, query):
        requested.append(query)
        if upstream["state"] == "timeout":
            raise TimeoutError("synthetic timeout")
        if upstream["state"] == "bad_crs":
            return dict(fixture, crs={"properties": {"name": "EPSG:3826"}}), TIME
        return fixture if upstream["state"] != "empty" else collection(), TIME, GeologyDatasetEvidence(
            "synthetic", "fixture-v1", None, complete=True)

    monkeypatch.setattr(GeologyCloudAdapter, "fetch", fetch)
    args = {"mode": "screening", "buffer_m": 50, "limit": 10}
    live = client.get(url, args).json()
    cached = client.get(url, args).json()
    assert cached["data_quality"]["cache"] == "fresh_cache"
    assert len(requested) == 1
    assert live["screening_context"]["published_version"] == 1
    bounds = ",".join(map(str,live["screening_context"]["query_bbox"]))
    exploration = client.get(url, {"mode": "exploration", "bbox": bounds, "limit": 10}).json()
    assert len(requested) == 2 and requested[0] == requested[1]
    assert exploration["screening_context"]["mode"] == "exploration"

    source = geology_source(project.__class__.objects.get(pk=project.pk))[0]
    analysis = fixed_screening_context(project, source, worksite_geometry_hash(source.geometry), 50)
    scope = GeologyCloudService().prepare_cache_context(
        "drill", bbox=analysis.bbox, project=project, limit=10, publication=(source,None), analysis=analysis,
    )
    cache.delete(scope["cache_key"])
    upstream["state"] = "timeout"
    stale = client.get(url, args).json()
    assert stale["data_quality"]["cache"] == "stale_cache" and stale["data_quality"]["failure_reason"] == "timeout"
    assert stale["screening_context"]["geometry_hash"] == live["screening_context"]["geometry_hash"]
    timeout = client.get(url, {**args, "buffer_m": 51}).json()
    assert timeout["data_quality"]["cache"] is None and timeout["data_quality"]["failure_reason"] == "timeout"
    upstream["state"] = "bad_crs"
    invalid = client.get(url, {**args, "buffer_m": 51}).json()
    assert invalid["data_quality"]["failure_reason"] == "unsupported_crs"
    upstream["state"] = "empty"
    empty = client.get(url, {**args, "buffer_m": 51}).json()
    assert empty["data_quality"]["retrieval"] == "empty" and empty["features"] == []

    plan = revision.plan
    new = PlanVersion.objects.create(plan=plan,name="Synthetic published v2",version=2,status="draft",geometry=plan.geometry)
    actor = plan.created_by
    event = PlanWorkflowEvent.objects.create(plan=plan,revision=new,actor=actor,actor_label=actor.username,action="published")
    Plan.objects.filter(pk=plan.pk).update(publication_event=event,status="published")
    upstream["state"] = "ok"
    next_result = client.get(url,args).json()
    assert next_result["screening_context"]["revision_id"] == new.pk
    assert next_result["screening_context"]["published_version"] == 2
    assert next_result["data_quality"]["cache"] == "live"
    assert next_result["screening"]["nearby_feature_count"] == 0
    Plan.objects.filter(pk=plan.pk).update(status="archived")
    archived = client.get(url,args).json()
    assert archived["status"] == "unavailable" and archived["data_quality"]["failure_reason"] == "source_archived"
    assert archived["features"] == [] and archived["screening_context"]["geometry"] is None
