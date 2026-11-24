import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import OperationalError
from django.utils import timezone

from worksite_gis.integrations.geology_cloud import GeologyCloudAdapter
from worksite_gis.models import Plan, PlanVersion, PlanWorkflowEvent, Project
from worksite_gis.services.geology_screening import worksite_geometry_hash
from worksite_gis.tests.test_worksite_versioning import worksite


@pytest.fixture
def workflow(client, django_user_model):
    users = {role: django_user_model.objects.create_user(username=role, is_staff=role == "manager")
             for role in ("owner", "manager", "reader")}
    project = Project.objects.create(slug="acceptance", name="Acceptance", owner=users["owner"])
    base = "/api/projects/acceptance/"

    def call(role, method, endpoint, body=None, token=None):
        client.force_login(users[role])
        headers = {} if token is None else {"X-WORKSITE-Lock-Version": str(token)}
        if method == "get":
            return client.get(base + endpoint, body or {}, headers=headers)
        return getattr(client, method)(base + endpoint, body or {}, content_type="application/json", headers=headers)

    return project, users, call


@pytest.mark.django_db
def test_owner_published_revision_and_all_layers_keep_immutable_snapshot(workflow, monkeypatch):
    project, users, call = workflow
    cache.clear()
    bbox = {"bbox": "120.98,24.99,121.08,25.03"}
    layers = ("drill", "liquefaction", "dip_slope", "rock_fall", "debris_flow_track")
    point = {"type": "Point", "coordinates": [121.004, 25.004]}
    polygon = worksite(121)["features"][0]["geometry"]
    geometry = dict(zip(layers, (point, polygon, polygon, polygon, polygon)))
    calls = []
    upstream = {"mode": "live"}

    def fetch(adapter, query):
        calls.append((adapter.layer, dict(query)))
        if upstream["mode"] == "error":
            raise TimeoutError("synthetic timeout")
        features = [] if upstream["mode"] == "empty" else [
            {"type": "Feature", "geometry": geometry[adapter.layer], "properties": {}},
        ]
        return {"type": "FeatureCollection", "features": features}, timezone.now()

    monkeypatch.setattr(GeologyCloudAdapter, "fetch", fetch)

    def create(name, longitude):
        response = call("owner", "post", "plans/", {"name": name, "geometry": worksite(longitude), "metadata": {"engineering": name}})
        assert response.status_code == 201, response.content
        return response.json()["plan"]

    def write(role, plan, action, next_plan, body=None, method="post"):
        response = call(role, method, f"plans/{plan['id']}/{action}", body, plan["lock_version"])
        assert response.status_code == next_plan, response.content
        return response.json().get("plan")

    plan = create("Public original", 121)
    plan_id = plan["id"]
    detail = f"plans/{plan_id}/"
    v1 = PlanVersion.objects.filter(plan_id=plan_id).values().get()
    submitted = write("owner", plan, "submit/", 200)
    write("owner", plan, "submit/", 409)
    published = write("manager", submitted, "review/", 200, {"decision": "publish"})
    write("manager", submitted, "review/", 409, {"decision": "publish"})
    write("reader", published, "", 403, {"name": "blocked"}, "patch")
    write("owner", published, "geology-source/", 403)
    selected = write("manager", published, "geology-source/", 200)
    v2draft = write("owner", selected, "", 200, {"name": "private v2", "geometry": worksite(121.04), "metadata": {"engineering": "private marker"}}, "patch")
    assert (v2draft["status"], v2draft["version"]) == ("draft", 2)
    submitted2 = write("owner", v2draft, "submit/", 200)
    returned2 = write("manager", submitted2, "review/", 200, {"decision": "return", "reason": "private return"})
    assert returned2["version"] == 2

    listed = call("reader", "get", "plans/").json()["plans"][0]
    assert listed["name"] == v1["name"] and listed["geometry"] == v1["geometry"]
    assert listed["version"] == 1 and listed["return_reason"] is None
    assert "lock_version" not in listed and "engineering" not in listed["metadata"]
    versions = call("reader", "get", detail + "versions/").json()
    assert [row["version"] for row in versions["versions"]] == [1] and "events" not in versions
    export = call("reader", "get", detail + "export/")
    assert export.json() == v1["geometry"] and '-v1.geojson' in export["Content-Disposition"]

    def assert_snapshot(plan_data, expected_intersection):
        expected = f"verified:{plan_id}:{plan_data['version']}:{worksite_geometry_hash(plan_data['geometry'])}"
        summary = call("reader", "get", "geology/summary/", bbox)
        assert summary["X-WORKSITE-Worksite"] == expected
        assert len(summary.json()["layers"]) == len(layers)
        assert all(row["screening"]["intersects"] is expected_intersection for row in summary.json()["layers"])
        for layer in layers:
            layer_response = call("reader", "get", f"geology/{layer}/", bbox)
            assert layer_response["X-WORKSITE-Worksite"] == expected
            assert set(layer_response.json()) == {"status", "fetched_at", "source", "features", "screening", "error"}
            assert layer_response.json()["status"] == "cached"
            assert layer_response.json()["screening"]["intersects"] is expected_intersection
        rows = {row["layer"]: row for row in summary.json()["layers"]}
        assert rows["drill"]["screening"]["nearest_distance_m"] == 0 if expected_intersection else rows["drill"]["screening"]["nearest_distance_m"] > 1000
        liq = rows["liquefaction"]["screening"]
        # Legacy viewport requests keep the response shape, with no certainty claim.
        assert liq["intersection_ratio"] is None and liq["intersection_area_m2"] is None
        assert liq["overlap_reason"] == "viewport_exploration"
        return expected

    ref1 = assert_snapshot(published, True)
    assert sorted(layer for layer, _ in calls) == sorted(layers)
    resubmitted = write("owner", returned2, "submit/", 200)
    published2 = write("manager", resubmitted, "review/", 200, {"decision": "publish"})
    # The prior publication's cache (including stale entries) must not serve v2.
    upstream["mode"] = "error"
    no_fallback = call("reader", "get", "geology/drill/", bbox)
    assert no_fallback.json()["status"] == "error" and no_fallback["X-WORKSITE-Worksite"] != ref1
    upstream["mode"] = "live"
    ref2 = assert_snapshot(published2, False)
    assert ref1 != ref2
    cache.clear()
    upstream["mode"] = "empty"
    empty = call("reader", "get", "geology/drill/", bbox)
    assert empty["X-WORKSITE-Worksite"] == ref2 and empty.json()["features"] == []
    from worksite_gis.services.geology_screening import GeologyCloudService
    project.refresh_from_db()
    key = GeologyCloudService().prepare_cache_context("drill", bbox=bbox["bbox"], project=project, limit=200)["cache_key"]
    cache.delete(key)
    upstream["mode"] = "error"
    stale = call("reader", "get", "geology/drill/", bbox)
    assert stale.json()["status"] == "stale" and stale["X-WORKSITE-Worksite"] == ref2
    assert call("reader", "get", detail + "export/").json() == published2["geometry"]

    other = create("Second plan", 121.02)
    other = write("owner", other, "submit/", 200)
    other = write("manager", other, "review/", 200, {"decision": "publish"})
    project.refresh_from_db()
    assert project.geology_source_plan_id == plan_id
    archived = write("manager", published2, "archive/", 200)
    write("manager", published2, "archive/", 409)
    write("manager", archived, "archive/", 400)
    assert archived["version"] == Plan.objects.get(pk=plan_id).version == 2
    no_source = call("reader", "get", "geology/summary/", bbox)
    assert no_source["X-WORKSITE-Worksite-Reason"] == "source_archived"
    assert no_source.json()["status"] == "unavailable"
    other = write("manager", other, "geology-source/", 200)
    assert call("reader", "get", "geology/summary/", bbox)["X-WORKSITE-Worksite"].startswith(f"verified:{other['id']}:1:")
    events = list(PlanWorkflowEvent.objects.filter(plan_id=plan_id).values_list("action", "revision__version", "actor_id", "reason", "created_at"))
    assert [(action, rev) for action, rev, *_ in events] == [
        ("created", 1), ("submitted", 1), ("published", 1), ("source_selected", 1),
        ("submitted", 2), ("returned", 2), ("resubmitted", 2), ("published", 2), ("archived", 2),
    ]
    assert events[5][2:4] == (users["manager"].pk, "private return")
    assert [row[-1] for row in events] == sorted(row[-1] for row in events)
    assert PlanVersion.objects.filter(pk=v1["id"]).values().get() == v1
    assert PlanWorkflowEvent.objects.get(plan_id=other["id"], action="source_selected").actor_id == users["manager"].pk
    cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["history", "source_switch", "publisher_db"])
def test_other_failures_roll_back_all_publication_rows(workflow, monkeypatch, failure):
    from worksite_gis import views_plans

    project, users, call = workflow
    plan = call("owner", "post", "plans/", {"name": "Original", "geometry": worksite(121)}).json()["plan"]
    path = f"plans/{plan['id']}/"
    if failure != "history":
        plan = call("owner", "post", path + "submit/", token=plan["lock_version"]).json()["plan"]
    if failure == "source_switch":
        call("manager", "post", path + "review/", {"decision": "publish"}, plan["lock_version"])
        second = call("owner", "post", "plans/", {"name": "Existing source", "geometry": worksite(121.03)}).json()["plan"]
        second = call("owner", "post", f"plans/{second['id']}/submit/", token=second["lock_version"]).json()["plan"]
        second = call("manager", "post", f"plans/{second['id']}/review/", {"decision": "publish"}, second["lock_version"]).json()["plan"]
        call("manager", "post", f"plans/{second['id']}/geology-source/", token=second["lock_version"])
        plan["lock_version"] += 1
    snapshot = lambda: [list(model.objects.order_by("pk").values()) for model in (Project, Plan, PlanVersion, PlanWorkflowEvent)]
    before = snapshot()
    error_type = OperationalError if failure == "publisher_db" else RuntimeError

    def explode(*args, **kwargs):
        raise error_type("injected failure")

    if failure == "history":
        monkeypatch.setattr(views_plans, "_create_revision", explode)
        request = lambda: call("owner", "patch", path, {"name": "Changed"}, plan["lock_version"])
    elif failure == "publisher_db":
        monkeypatch.setattr(views_plans, "_write_plan", explode)
        request = lambda: call("manager", "post", path + "review/", {"decision": "publish"}, plan["lock_version"])
    else:
        original_save = Project.save

        def fail_source(instance, **kwargs):
            assert Plan.objects.get(pk=plan["id"]).lock_version == plan["lock_version"] + 1
            assert PlanWorkflowEvent.objects.filter(plan_id=plan["id"], action="source_selected").exists()
            explode()

        monkeypatch.setattr(Project, "save", fail_source)
        request = lambda: call("manager", "post", path + "geology-source/", token=plan["lock_version"])

    with pytest.raises(error_type, match="injected failure"):
        request()
    assert snapshot() == before
