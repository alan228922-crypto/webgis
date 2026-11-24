import pytest
from django.urls import reverse
from django.utils import timezone

from worksite_gis.integrations.geology_cloud import GeologyCloudAdapter
from worksite_gis.models import Plan, PlanVersion, PlanWorkflowEvent, Project
from worksite_gis.permissions import DEVELOPER_GROUP, MANAGER_GROUP, ensure_role_groups


def make_project(user):
    return Project.objects.create(slug="workflow", name="Workflow", owner=user)


def worksite_geometry():
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"source": "test survey"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[121, 25], [121.01, 25], [121.01, 25.01], [121, 25.01], [121, 25]]],
            },
        }],
    }


def workflow_rows():
    return [
        list(model.objects.order_by("pk").values())
        for model in (Project, Plan, PlanVersion)
    ]


def plan_write(client, method, url, *args, **kwargs):
    plan_id = int(url.split("/plans/", 1)[1].split("/")[0])
    token = Plan.objects.get(pk=plan_id).lock_version
    return getattr(client, method)(url, *args, headers={"X-WORKSITE-Lock-Version": str(token)}, **kwargs)


@pytest.mark.django_db
@pytest.mark.parametrize("is_staff", [False, True])
@pytest.mark.parametrize("status", [None, "draft", "active", "review", "submitted", "rejected", "needs_revision", "approved", "published", "archived"])
def test_plan_creation_only_accepts_draft(client, django_user_model, is_staff, status):
    user = django_user_model.objects.create_user(username="creator", is_staff=is_staff)
    project = make_project(user)
    client.force_login(user)
    before = workflow_rows()
    payload = {"name": "New draft", "geometry": worksite_geometry()}
    if status is not None:
        payload["status"] = status
    response = client.post("/api/projects/workflow/plans/", payload, content_type="application/json")
    if status in {None, "draft", "active"}:
        assert response.status_code == 201
        assert response.json()["plan"]["status"] == "draft"
        assert project.plans.get().versions.get().status == "draft"
    else:
        assert response.status_code == 400
        assert workflow_rows() == before


@pytest.mark.django_db
@pytest.mark.parametrize("is_staff", [False, True])
@pytest.mark.parametrize("target", ["review", "approved", "published", "rejected", "archived"])
def test_plan_content_update_cannot_change_workflow_state(client, django_user_model, is_staff, target):
    user = django_user_model.objects.create_user(username="editor", is_staff=is_staff)
    project = make_project(user)
    plan = Plan.objects.create(project=project, name="Draft", created_by=user, geometry=worksite_geometry())
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="draft")
    client.force_login(user)
    before = workflow_rows()
    response = client.patch(
        f"/api/projects/workflow/plans/{plan.pk}/",
        {"name": "Revised", "status": target}, content_type="application/json",
    )
    assert response.status_code == 400
    assert workflow_rows() == before


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_geometry", ["empty", "point", "line", "invalid_polygon", "outside_wgs84", "untraceable"])
def test_review_rejects_ineligible_worksite_without_changing_rows(client, django_user_model, invalid_geometry):
    manager = django_user_model.objects.create_user(username="manager", is_staff=True)
    project = make_project(manager)
    geometry = worksite_geometry()
    feature = geometry["features"][0]
    if invalid_geometry == "empty":
        geometry["features"] = []
    elif invalid_geometry == "point":
        feature["geometry"] = {"type": "Point", "coordinates": [121, 25]}
    elif invalid_geometry == "line":
        feature["geometry"] = {"type": "LineString", "coordinates": [[121, 25], [121.01, 25]]}
    elif invalid_geometry == "invalid_polygon":
        feature["geometry"]["coordinates"] = [[[121, 25], [121.01, 25.01], [121.01, 25], [121, 25.01], [121, 25]]]
    elif invalid_geometry == "outside_wgs84":
        feature["geometry"]["coordinates"] = [[[121, 91], [122, 91], [122, 92], [121, 92], [121, 91]]]
    else:
        feature["properties"] = {}
    plan = Plan.objects.create(project=project, name="Submitted", status="submitted", geometry=geometry)
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="submitted", geometry=geometry)
    client.force_login(manager)
    before = workflow_rows()
    response = plan_write(client, "post",
        f"/api/projects/workflow/plans/{plan.pk}/review/",
        {"decision": "publish"}, content_type="application/json",
    )
    assert response.status_code == 400
    assert workflow_rows() == before


@pytest.mark.django_db
def test_plan_admin_is_read_only(client, django_user_model):
    manager = django_user_model.objects.create_user(username="manager", is_staff=True)
    ensure_role_groups()[MANAGER_GROUP].user_set.add(manager)
    project = make_project(manager)
    plan = Plan.objects.create(project=project, name="Draft", created_by=manager)
    client.force_login(manager)
    add_url = reverse("admin:worksite_gis_plan_add")
    change_url = reverse("admin:worksite_gis_plan_change", args=[plan.pk])
    delete_url = reverse("admin:worksite_gis_plan_delete", args=[plan.pk])
    assert client.get(change_url).status_code == 200
    assert "前往工區規劃管理" in client.get(change_url).content.decode()
    assert client.get(delete_url).status_code == 403
    before = workflow_rows()
    for url in (add_url, change_url, delete_url):
        assert client.post(url, {"name": "Unauthorized change"}).status_code == 403
    assert workflow_rows() == before


@pytest.mark.django_db
def test_repeating_published_status_does_not_create_another_published_version(client, django_user_model):
    manager = django_user_model.objects.create_user(username="manager", is_staff=True)
    project = make_project(manager)
    plan = Plan.objects.create(project=project, name="Published", status="published", geometry=worksite_geometry())
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="published", geometry=plan.geometry)
    client.force_login(manager)
    before = workflow_rows()
    response = plan_write(client, "patch",
        f"/api/projects/workflow/plans/{plan.pk}/", {"status": "approved"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert workflow_rows() == before


@pytest.mark.django_db
def test_privileged_repeated_submit_and_review_of_draft_are_rejected(client, django_user_model):
    manager = django_user_model.objects.create_user(username="manager", is_staff=True)
    project = make_project(manager)
    plan = Plan.objects.create(project=project, name="Draft", geometry=worksite_geometry(), created_by=manager)
    client.force_login(manager)
    base = f"/api/projects/workflow/plans/{plan.pk}/"
    before = workflow_rows()
    assert plan_write(client, "post", base + "review/", {"decision": "publish"}, content_type="application/json").status_code == 400
    assert workflow_rows() == before
    assert plan_write(client, "post", base + "submit/").status_code == 200
    before = workflow_rows()
    assert plan_write(client, "post", base + "submit/").status_code == 400
    assert workflow_rows() == before


@pytest.mark.django_db
def test_draft_preserves_point_and_line_annotations(client, django_user_model):
    owner = django_user_model.objects.create_user(username="drawer")
    project = make_project(owner)
    client.force_login(owner)
    shape_types = (
        ("Point", [121, 25]),
        ("LineString", [[121, 25], [121.01, 25]]),
    )
    geometry = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": kind, "coordinates": coordinates}}
            for kind, coordinates in shape_types
        ],
    }
    response = client.post(
        "/api/projects/workflow/plans/", {"name": "Annotations", "geometry": geometry},
        content_type="application/json",
    )
    assert response.status_code == 201
    saved = project.plans.get()
    assert saved.status == "draft"
    assert [feature["geometry"]["type"] for feature in saved.geometry["features"]] == ["Point", "LineString"]


@pytest.mark.django_db
@pytest.mark.parametrize("formal_status", ["published", "approved"])
def test_geology_uses_formal_worksite_and_excludes_private_drafts(
    client, django_user_model, monkeypatch, formal_status
):
    owner = django_user_model.objects.create_user(username="geology-owner")
    reviewer = django_user_model.objects.create_user(username="geology-reviewer", is_staff=True)
    reader = django_user_model.objects.create_user(username="geology-reader")
    project = make_project(owner)
    geometry = worksite_geometry()
    geometry["features"][0]["properties"]["administrative_area"] = "test area"
    base = "/api/projects/workflow/"
    bbox = {"bbox": "120.99,24.99,121.03,25.03"}

    def fake_fetch(adapter, query):
        features = [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [121.005, 25.005]},
            "properties": {"Project_No": "GEOTEST", "Hole_Point_No": "H1"},
        }] if adapter.layer == "drill" else []
        return {"type": "FeatureCollection", "features": features}, timezone.now()

    monkeypatch.setattr(GeologyCloudAdapter, "fetch", fake_fetch)
    client.force_login(owner)
    before = workflow_rows()
    rejected = client.post(
        base + "plans/", {"name": "Private", "status": "approved", "geometry": geometry},
        content_type="application/json",
    )
    assert rejected.status_code == 400
    assert workflow_rows() == before
    private = Plan.objects.create(project=project, name="Private", created_by=owner, geometry=geometry)
    client.force_login(reader)
    assert client.get(base + "geology/summary/", bbox).json()["status"] == "unavailable"
    assert client.get(base + "plans/").json()["plans"] == []
    assert client.get(base + f"plans/{private.pk}/export/").status_code == 404

    if formal_status == "approved":
        Plan.objects.create(project=project, name="Legacy", status="approved", created_by=owner, geometry=geometry)
    else:
        client.force_login(owner)
        created = client.post(
            base + "plans/", {"name": "Formal", "geometry": geometry}, content_type="application/json",
        )
        assert created.status_code == 201
        plan_path = base + f'plans/{created.json()["plan"]["id"]}/'
        assert plan_write(client, "post", plan_path + "submit/").status_code == 200
        client.force_login(reviewer)
        assert plan_write(client, "post", plan_path + "review/", {"decision": "publish"}, content_type="application/json").status_code == 200
        assert plan_write(client, "post", plan_path + "geology-source/").status_code == 200

    client.force_login(reader)
    layer = client.get(base + "geology/drill/", bbox)
    summary = client.get(base + "geology/summary/", bbox)
    assert layer.status_code == summary.status_code == 200
    assert set(layer.json()) == {"status", "fetched_at", "source", "features", "screening", "error"}
    assert layer.json()["screening"]["intersects"] is True
    assert set(summary.json()) == {"project", "status", "generated_at", "cards", "layers", "actions", "limitations"}
    assert summary.json()["project"]["geometry_available"] is True
    assert len(summary.json()["layers"]) == 5


@pytest.mark.django_db
def test_owner_can_submit_draft_and_modify_needs_revision(client, django_user_model):
    user = django_user_model.objects.create_user(username="submitter")
    client.force_login(user)
    project = make_project(user)
    plan = Plan.objects.create(project=project, name="Draft", created_by=user, updated_by=user, geometry=worksite_geometry())

    submitted = plan_write(client, "post", f"/api/projects/workflow/plans/{plan.pk}/submit/")
    assert submitted.status_code == 200
    assert submitted.json()["plan"]["status"] == "submitted"

    blocked = client.patch(f"/api/projects/workflow/plans/{plan.pk}/", data={"name": "Blocked"}, content_type="application/json")
    assert blocked.status_code == 403

    manager = django_user_model.objects.create_user(username="reviewer", is_staff=True)
    ensure_role_groups()[MANAGER_GROUP].user_set.add(manager)
    client.force_login(manager)
    returned = plan_write(client, "post",
        f"/api/projects/workflow/plans/{plan.pk}/review/",
        data={"status": "needs_revision", "reason": "請補充工區說明"},
        content_type="application/json",
    )
    assert returned.status_code == 200
    assert returned.json()["plan"]["status"] == "needs_revision"
    assert returned.json()["plan"]["return_reason"] == "請補充工區說明"
    assert PlanWorkflowEvent.objects.get(plan=plan, action="returned").reason == "請補充工區說明"
    assert PlanVersion.objects.get(plan=plan, version=1).return_reason is None

    client.force_login(user)
    updated = plan_write(client, "patch", f"/api/projects/workflow/plans/{plan.pk}/", data={"name": "Revised"}, content_type="application/json")
    resubmitted = plan_write(client, "post", f"/api/projects/workflow/plans/{plan.pk}/submit/")
    assert updated.status_code == 200
    assert resubmitted.status_code == 200
    assert resubmitted.json()["plan"]["status"] == "submitted"

    client.force_login(manager)
    published = plan_write(client, "post",
        f"/api/projects/workflow/plans/{plan.pk}/review/",
        {"status": "approved"}, content_type="application/json",
    )
    assert published.status_code == 200
    assert published.json()["plan"]["status"] == "published"


@pytest.mark.django_db
def test_manager_can_publish_and_history_is_preserved(client, django_user_model):
    owner = django_user_model.objects.create_user(username="publisher-owner")
    manager = django_user_model.objects.create_user(username="publisher", is_staff=True)
    ensure_role_groups()[MANAGER_GROUP].user_set.add(manager)
    project = make_project(owner)
    plan = Plan.objects.create(project=project, name="Version one", created_by=owner, updated_by=owner, geometry=worksite_geometry())
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="draft", geometry=plan.geometry, created_by=owner)
    client.force_login(manager)

    submitted = plan_write(client, "post", f"/api/projects/workflow/plans/{plan.pk}/submit/")
    published = plan_write(client, "post",
        f"/api/projects/workflow/plans/{plan.pk}/review/",
        data={"status": "published"},
        content_type="application/json",
    )

    assert submitted.status_code == 200
    assert published.status_code == 200
    assert published.json()["plan"]["status"] == "published"
    assert PlanVersion.objects.filter(plan=plan, version=1).exists()
    assert PlanVersion.objects.get(plan=plan, version=1).status == "draft"
    assert PlanWorkflowEvent.objects.get(plan=plan, action="published").revision.version == 1

    edited = plan_write(client, "patch",
        f"/api/projects/workflow/plans/{plan.pk}/",
        data={"name": "Version two"},
        content_type="application/json",
    )
    assert edited.status_code == 200
    assert edited.json()["plan"]["status"] == "draft"
    assert list(PlanVersion.objects.filter(plan=plan).values_list("version", "status")) == [(1, "draft"), (2, "draft")]


@pytest.mark.django_db
def test_regular_user_sees_only_latest_published_version(client, django_user_model):
    owner = django_user_model.objects.create_user(username="version-reader")
    project = make_project(owner)
    plan = Plan.objects.create(project=project, name="Published", status="published", version=2, created_by=owner)
    PlanVersion.objects.create(plan=plan, version=1, name="Old", status="published", geometry={})
    PlanVersion.objects.create(plan=plan, version=2, name="Latest", status="published", geometry={})
    PlanVersion.objects.create(plan=plan, version=3, name="Draft", status="draft", geometry={})
    reader = django_user_model.objects.create_user(username="published-only-reader")
    client.force_login(reader)

    response = client.get(f"/api/projects/workflow/plans/{plan.pk}/versions/")

    assert response.status_code == 200
    assert [item["version"] for item in response.json()["versions"]] == [2]


@pytest.mark.django_db
@pytest.mark.parametrize("group_name", [MANAGER_GROUP, DEVELOPER_GROUP])
def test_manager_and_developer_can_review_publish_and_view_history(client, django_user_model, group_name):
    owner = django_user_model.objects.create_user(username=f"owner-{group_name}")
    reviewer = django_user_model.objects.create_user(username=f"reviewer-{group_name}", is_staff=True)
    ensure_role_groups()[group_name].user_set.add(reviewer)
    project = make_project(owner)
    plan = Plan.objects.create(project=project, name="Review me", status="submitted", created_by=owner, geometry=worksite_geometry())
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="submitted", geometry=plan.geometry)
    client.force_login(reviewer)

    published = plan_write(client, "post",
        f"/api/projects/workflow/plans/{plan.pk}/review/",
        data={"status": "published"},
        content_type="application/json",
    )
    history = client.get(f"/api/projects/workflow/plans/{plan.pk}/versions/")

    assert published.status_code == 200
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 1


@pytest.mark.django_db
def test_regular_user_cannot_review_or_publish(client, django_user_model):
    owner = django_user_model.objects.create_user(username="cannot-publish")
    project = make_project(owner)
    plan = Plan.objects.create(project=project, name="No publish", created_by=owner)
    client.force_login(owner)

    response = plan_write(client, "post",
        f"/api/projects/workflow/plans/{plan.pk}/review/",
        data={"status": "published"},
        content_type="application/json",
    )

    assert response.status_code == 403
    assert Plan.objects.get(pk=plan.pk).status == "draft"


@pytest.mark.django_db
def test_manager_can_archive_plan_without_overwriting_published_history(client, django_user_model):
    owner = django_user_model.objects.create_user(username="archive-owner")
    manager = django_user_model.objects.create_user(username="archive-manager", is_staff=True)
    ensure_role_groups()[MANAGER_GROUP].user_set.add(manager)
    project = make_project(owner)
    plan = Plan.objects.create(project=project, name="Archive me", status="published", created_by=owner)
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="published", geometry={})
    client.force_login(manager)

    response = plan_write(client, "post", f"/api/projects/workflow/plans/{plan.pk}/archive/")

    assert response.status_code == 200
    assert response.json()["plan"]["status"] == "archived"
    assert list(PlanVersion.objects.filter(plan=plan).values_list("version", "status")) == [(1, "published")]
    assert Plan.objects.get(pk=plan.pk).version == 1
    assert PlanWorkflowEvent.objects.get(plan=plan, action="archived").revision.version == 1
