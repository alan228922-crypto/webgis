import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection
from pathlib import Path

from worksite_gis.models import Plan, PlanVersion, Project
from worksite_gis.permissions import DEVELOPER_GROUP, MANAGER_GROUP, ensure_role_groups


def _group_user(user, group_name):
    ensure_role_groups()[group_name].user_set.add(user)


def _metadata_by_id(response):
    return {item["id"]: item for item in response.json()["plans"]}


@pytest.mark.django_db
def test_ui_metadata_requires_authentication_and_returns_json(client):
    response = client.get("/api/projects/worksite/plans/ui-metadata/")

    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/json")
    assert "plans" not in response.json()


@pytest.mark.django_db
def test_regular_user_metadata_uses_backend_visibility_and_capabilities(client, django_user_model):
    user = django_user_model.objects.create_user(username="metadata-owner")
    other = django_user_model.objects.create_user(username="metadata-other")
    project = Project.objects.create(slug="metadata-project", name="Metadata project", owner=user)
    own_draft = Plan.objects.create(project=project, name="Own draft", created_by=user)
    own_revision = Plan.objects.create(project=project, name="Own revision", status="needs_revision", created_by=user)
    own_submitted = Plan.objects.create(project=project, name="Own submitted", status="submitted", created_by=user)
    other_draft = Plan.objects.create(project=project, name="Other draft", created_by=other)
    published = Plan.objects.create(project=project, name="Published", status="published", created_by=other)
    PlanVersion.objects.create(plan=published, version=1, name=published.name, status="published", geometry={})
    archived = Plan.objects.create(project=project, name="Archived", status="archived", created_by=other)

    client.force_login(user)
    response = client.get(f"/api/projects/{project.slug}/plans/ui-metadata/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == {"slug": project.slug, "can_create_plan": True}
    plans = _metadata_by_id(response)
    assert archived.id in plans  # Owner reads archived Plans in an active Project.
    assert plans[archived.id]["capabilities"]["can_edit"] is False
    for item in plans.values():
        assert isinstance(item["id"], int)
        assert isinstance(item["status"], str)
        assert all(isinstance(value, bool) for value in item["capabilities"].values())
        assert not {"owner_id", "created_by_id", "email"}.intersection(item)

    assert plans[own_draft.id]["capabilities"]["can_edit"] is True
    assert plans[own_draft.id]["capabilities"]["can_submit"] is True
    assert plans[own_revision.id]["capabilities"]["can_edit"] is True
    assert plans[own_revision.id]["capabilities"]["can_submit"] is True
    assert plans[own_submitted.id]["capabilities"]["can_edit"] is False
    assert plans[own_submitted.id]["capabilities"]["can_submit"] is False
    assert other_draft.id in plans  # The Project owner can read it.
    assert plans[other_draft.id]["capabilities"]["can_edit"] is False
    assert plans[published.id]["capabilities"]["can_export"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("group_name", [MANAGER_GROUP, DEVELOPER_GROUP])
def test_manager_and_developer_metadata_exposes_allowed_workflow_capabilities(client, django_user_model, group_name):
    user = django_user_model.objects.create_user(username=f"metadata-{group_name}", is_staff=True)
    _group_user(user, group_name)
    role_slug = "manager" if group_name == MANAGER_GROUP else "developer"
    project = Project.objects.create(slug=f"metadata-{role_slug}", name="Metadata project")
    plan = Plan.objects.create(project=project, name="Submitted", status="submitted")
    PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="submitted", geometry={})

    client.force_login(user)
    response = client.get(f"/api/projects/{project.slug}/plans/ui-metadata/")

    assert response.status_code == 200
    capabilities = _metadata_by_id(response)[plan.id]["capabilities"]
    assert capabilities["can_edit"] is True
    assert capabilities["can_review"] is True
    assert capabilities["can_publish"] is True
    assert capabilities["can_archive"] is True
    assert capabilities["can_view_versions"] is True
    assert capabilities["can_export"] is True
    assert "role" not in response.json()


@pytest.mark.django_db
def test_ui_metadata_missing_project_uses_json_not_found(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="metadata-missing"))

    response = client.get("/api/projects/missing/plans/ui-metadata/")

    assert response.status_code == 404
    assert response.headers["Content-Type"].startswith("application/json")


@pytest.mark.django_db
def test_ui_metadata_does_not_add_per_plan_queries(client, django_user_model):
    user = django_user_model.objects.create_user(username="metadata-query-count")
    project = Project.objects.create(slug="metadata-query-count", name="Metadata project")
    Plan.objects.create(project=project, name="Plan 1", created_by=user)
    client.force_login(user)

    with CaptureQueriesContext(connection) as one_plan_context:
        client.get(f"/api/projects/{project.slug}/plans/ui-metadata/")
    for index in range(2, 6):
        Plan.objects.create(project=project, name=f"Plan {index}", created_by=user)
    with CaptureQueriesContext(connection) as five_plan_context:
        client.get(f"/api/projects/{project.slug}/plans/ui-metadata/")

    assert len(five_plan_context) <= len(one_plan_context) + 2


def test_plans_javascript_uses_backend_capabilities_and_metadata_endpoint():
    script = (Path(__file__).parents[2] / "home/static/home/js/maintainer_plans.js").read_text(encoding="utf-8")

    assert "ui-metadata/" in script
    for capability in ("can_edit", "can_submit", "can_review", "can_publish", "can_archive", "can_delete", "can_view_versions", "can_export"):
        assert capability in script
    assert "plans = data.plans || []" not in script
    assert "response.status === 401" in script
    assert "response.status === 403" in script
