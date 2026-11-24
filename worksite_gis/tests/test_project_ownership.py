import pytest
from django.test import Client

from worksite_gis.models import Plan, PlanVersion, Project
from worksite_gis.permissions import DEVELOPER_GROUP, MANAGER_GROUP, ensure_role_groups


def project_payload(slug):
    return {"slug": slug, "name": slug.title(), "center_latitude": 24.98, "center_longitude": 121.31}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("actor", "archived", "without_owner", "with_csrf", "expected"),
    [
        ("anonymous", False, False, True, 401),
        ("outsider", False, False, True, 403),
        ("owner", True, False, True, 404),
        ("outsider", False, True, True, 403),
        ("owner", False, False, False, 403),
        ("owner", False, False, True, 200),
        ("manager", False, True, True, 200),
    ],
)
def test_search_center_checks_csrf_and_object_access(
    django_user_model, actor, archived, without_owner, with_csrf, expected
):
    owner = django_user_model.objects.create_user(username="center-owner")
    project = Project.objects.create(
        slug="center", name="Center", owner=None if without_owner else owner,
        status="archived" if archived else "active",
    )
    client = Client(enforce_csrf_checks=True)
    if actor == "owner":
        client.force_login(owner)
    elif actor != "anonymous":
        user = django_user_model.objects.create_user(username=actor, is_staff=actor == "manager")
        client.force_login(user)
    assert client.get("/accounts/login/").status_code == 200
    headers = {"X-CSRFToken": client.cookies["csrftoken"].value} if with_csrf else {}
    row = Project.objects.filter(pk=project.pk)
    before = row.values().get()
    response = client.post(
        "/api/projects/center/search-center/",
        {"label": "Test center", "latitude": 24.98, "longitude": 121.31},
        content_type="application/json", headers=headers,
    )
    assert response.status_code == expected
    if expected == 200:
        assert set(response.json()) == {"project", "setup_status", "search_center"}
        assert row.values().get()["search_center_label"] == "Test center"
    else:
        assert row.values().get() == before
        assert response.headers["Content-Type"].startswith("application/json") is with_csrf


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["anonymous", "outsider", "owner", "creator", "manager"])
def test_plan_read_surfaces_share_private_visibility(client, django_user_model, actor):
    owner = django_user_model.objects.create_user(username="project-owner")
    creator = django_user_model.objects.create_user(username="plan-creator")
    project = Project.objects.create(slug="privacy", name="Privacy", owner=owner)
    geometry = {"type": "FeatureCollection", "features": []}
    plans = {}
    for name, status in [
        ("draft", "draft"), ("returned", "rejected"), ("published", "published"),
        ("legacy", "approved"), ("revising", "draft"),
    ]:
        plans[name] = Plan.objects.create(
            project=project, created_by=creator, name=name, status=status,
            geometry=geometry, metadata={"feature_count": 0, "review_notes": "internal note"},
            return_reason="internal reason",
        )
        PlanVersion.objects.create(
            plan=plans[name], version=1, name=name,
            status="published" if name == "revising" else status, geometry=geometry,
            created_by=creator, change_summary="internal summary", metadata=plans[name].metadata,
        )
    users = {"owner": owner, "creator": creator}
    if actor in {"outsider", "manager"}:
        users[actor] = django_user_model.objects.create_user(username=actor, is_staff=actor == "manager")
    if actor in users:
        client.force_login(users[actor])

    base = "/api/projects/privacy/plans/"
    listing = client.get(base)
    metadata = client.get(base + "ui-metadata/")
    private_reader = actor in {"owner", "creator", "manager"}
    if actor == "anonymous":
        assert listing.status_code == metadata.status_code == 401
    else:
        assert listing.status_code == metadata.status_code == 200
        expected_ids = {
            plan.id for plan in plans.values()
            if private_reader or plan.status in {"published", "approved"} or plan == plans["revising"]
        }
        assert {item["id"] for item in listing.json()["plans"]} == expected_ids
        assert {item["id"] for item in metadata.json()["plans"]} == expected_ids
        if not private_reader:
            assert all(item["return_reason"] is None for item in listing.json()["plans"])
            assert all(item["metadata"] == {"feature_count": 0} for item in listing.json()["plans"])

    for name, plan in plans.items():
        expected = 200 if private_reader or name in {"published", "legacy", "revising"} else 404
        if actor == "anonymous":
            expected = 401
        export = client.get(f"{base}{plan.pk}/export/")
        versions = client.get(f"{base}{plan.pk}/versions/")
        assert export.status_code == versions.status_code == expected
        if expected == 404:
            assert export.json() == client.get(f"{base}99999/export/").json()
            assert versions.json() == client.get(f"{base}99999/versions/").json()
        if expected == 200:
            assert export.json() == geometry
            assert export.headers["Content-Disposition"].endswith(f"-{plan.pk}-v1.geojson\"")
            version = versions.json()["versions"][0]
            assert version["change_summary"] == ("internal summary" if private_reader else "")
            assert version["created_by"] == (creator.username if private_reader else None)
            assert ("review_notes" in version["metadata"]) is private_reader
    if actor == "outsider":
        draft = plans["draft"]
        before = Project.objects.filter(pk=project.pk).values().get()
        plan_rows = list(project.plans.order_by("pk").values())
        version_rows = list(PlanVersion.objects.filter(plan__project=project).order_by("pk").values())
        for endpoint, method in [("", "patch"), ("submit/", "post"), ("review/", "post"), ("archive/", "post")]:
            response = getattr(client, method)(f"{base}{draft.pk}/{endpoint}")
            assert response.status_code == 404
        public_plan = plans["published"]
        assert client.patch(f"{base}{public_plan.pk}/").status_code == 403
        assert Project.objects.filter(pk=project.pk).values().get() == before
        assert list(project.plans.order_by("pk").values()) == plan_rows
        assert list(PlanVersion.objects.filter(plan__project=project).order_by("pk").values()) == version_rows


@pytest.mark.django_db
def test_parent_visibility_takes_priority_over_plan_creation_relationship(client, django_user_model):
    user = django_user_model.objects.create_user(username="archived-owner")
    project = Project.objects.create(slug="archived", name="Archived", owner=user, status="archived")
    plan = Plan.objects.create(project=project, name="Draft", created_by=user)
    client.force_login(user)
    before = Plan.objects.filter(pk=plan.pk).values().get()
    base = "/api/projects/archived/plans/"
    for path in ("", "ui-metadata/", f"{plan.pk}/export/", f"{plan.pk}/versions/"):
        assert client.get(base + path).status_code == 404
    assert client.patch(f"{base}{plan.pk}/").status_code == 404
    assert client.post(f"{base}{plan.pk}/submit/").status_code == 404
    assert Plan.objects.filter(pk=plan.pk).values().get() == before


@pytest.mark.django_db
def test_regular_user_can_create_and_edit_owned_project(client, django_user_model):
    user = django_user_model.objects.create_user(username="project-owner")
    client.force_login(user)

    created = client.post("/api/projects/", data=project_payload("owned"), content_type="application/json")
    updated = client.patch("/api/projects/owned/", data={"name": "Owned updated"}, content_type="application/json")

    assert created.status_code == 201
    assert Project.objects.get(slug="owned").owner_id == user.id
    assert updated.status_code == 200
    assert updated.json()["name"] == "Owned updated"


@pytest.mark.django_db
def test_regular_user_can_read_unarchived_but_not_archived_projects(client, django_user_model):
    user = django_user_model.objects.create_user(username="project-reader")
    client.force_login(user)
    Project.objects.create(slug="visible", name="Visible", status="active")
    Project.objects.create(slug="hidden", name="Hidden", status="archived")

    listing = client.get("/api/projects/")
    archived = client.get("/api/projects/hidden/")

    assert listing.status_code == 200
    assert [item["slug"] for item in listing.json()["projects"]] == ["visible"]
    assert archived.status_code == 404


@pytest.mark.django_db
def test_regular_user_can_create_plan_only_in_owned_project(client, django_user_model):
    user = django_user_model.objects.create_user(username="plan-owner")
    client.force_login(user)
    owned = Project.objects.create(slug="owned", name="Owned", owner=user)
    other = Project.objects.create(slug="other", name="Other")

    created = client.post("/api/projects/owned/plans/", data={"name": "Owned plan"}, content_type="application/json")
    forbidden = client.post("/api/projects/other/plans/", data={"name": "Other plan"}, content_type="application/json")
    own_plan = client.patch(
        f"/api/projects/owned/plans/{created.json()['plan']['id']}/",
        data={"name": "Owned plan updated"},
        headers={"X-WORKSITE-Lock-Version": "1"},
        content_type="application/json",
    )

    assert created.status_code == 201
    assert Plan.objects.get(project=owned).created_by_id == user.id
    assert forbidden.status_code == 403
    assert own_plan.status_code == 200
    assert not Plan.objects.filter(project=other).exists()


@pytest.mark.django_db
def test_regular_user_cannot_edit_other_project_or_plan(client, django_user_model):
    user = django_user_model.objects.create_user(username="limited-editor")
    client.force_login(user)
    other = Project.objects.create(slug="other", name="Other")
    plan = Plan.objects.create(project=other, name="Other plan", created_by=None)

    project_response = client.patch("/api/projects/other/", data={"name": "Nope"}, content_type="application/json")
    plan_response = client.patch(f"/api/projects/other/plans/{plan.pk}/", data={"name": "Nope"}, content_type="application/json")

    assert project_response.status_code == 403
    assert plan_response.status_code == 404
    assert Project.objects.get(pk=other.pk).name == "Other"
    assert Plan.objects.get(pk=plan.pk).name == "Other plan"


@pytest.mark.django_db
@pytest.mark.parametrize("group_name", [MANAGER_GROUP, DEVELOPER_GROUP])
def test_manager_and_developer_can_edit_other_data(client, django_user_model, group_name):
    groups = ensure_role_groups()
    user = django_user_model.objects.create_user(username=group_name, is_staff=True)
    groups[group_name].user_set.add(user)
    client.force_login(user)
    project = Project.objects.create(slug="managed", name="Managed")
    plan = Plan.objects.create(project=project, name="Managed plan")

    project_response = client.patch("/api/projects/managed/", data={"name": "Managed updated"}, content_type="application/json")
    plan_response = client.patch(f"/api/projects/managed/plans/{plan.pk}/", data={"name": "Plan updated"}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": "1"})

    assert project_response.status_code == 200
    assert plan_response.status_code == 200
