import pytest
from django.contrib import admin
from django.test import RequestFactory
from types import SimpleNamespace

from worksite_gis.models import Plan, PlanVersion, Project
from worksite_gis.permissions import (
    DEVELOPER_GROUP,
    MANAGER_GROUP,
    can_change_user_role,
    can_change_user_status,
    can_delete_user,
    ensure_role_groups,
    is_last_active_developer,
)


def make_project(owner, slug="deletable"):
    return Project.objects.create(slug=slug, name=slug.title(), owner=owner)


@pytest.mark.django_db
def test_regular_user_can_delete_own_safe_plan(client, django_user_model):
    user = django_user_model.objects.create_user(username="delete-owner")
    project = make_project(user)
    plan = Plan.objects.create(project=project, name="Draft", created_by=user)
    client.force_login(user)

    response = client.delete(f"/api/projects/{project.slug}/plans/{plan.pk}/", headers={"X-WORKSITE-Lock-Version": "1"})

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not Plan.objects.filter(pk=plan.pk).exists()


@pytest.mark.django_db
def test_regular_user_cannot_delete_other_plan_or_published_plan(client, django_user_model):
    owner = django_user_model.objects.create_user(username="other-owner")
    user = django_user_model.objects.create_user(username="delete-limited")
    project = make_project(owner, "other-project")
    other_plan = Plan.objects.create(project=project, name="Other", created_by=owner)
    client.force_login(user)

    forbidden = client.delete(
        f"/api/projects/{project.slug}/plans/{other_plan.pk}/",
        data='{"owner": 999, "status": "draft", "can_delete": true}',
        content_type="application/json",
    )
    assert forbidden.status_code == 404

    other_plan.created_by = user
    other_plan.status = "published"
    other_plan.save(update_fields=["created_by", "status"])
    conflict = client.delete(f"/api/projects/{project.slug}/plans/{other_plan.pk}/")
    assert conflict.status_code == 409
    assert Plan.objects.filter(pk=other_plan.pk).exists()


@pytest.mark.django_db
def test_project_delete_is_atomic_when_child_has_published_history(client, django_user_model):
    user = django_user_model.objects.create_user(username="project-delete")
    project = make_project(user, "protected-project")
    plan = Plan.objects.create(project=project, name="Published", status="published", created_by=user)
    version = PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="published", geometry={})
    client.force_login(user)

    response = client.delete(f"/api/projects/{project.slug}/")

    assert response.status_code == 409
    assert Project.objects.filter(pk=project.pk).exists()
    assert Plan.objects.filter(pk=plan.pk).exists()
    assert PlanVersion.objects.filter(pk=version.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("group_name", [MANAGER_GROUP, DEVELOPER_GROUP])
def test_privileged_user_can_delete_other_safe_project(client, django_user_model, group_name):
    groups = ensure_role_groups()
    actor = django_user_model.objects.create_user(username=f"actor-{group_name}", is_staff=True)
    groups[group_name].user_set.add(actor)
    owner = django_user_model.objects.create_user(username=f"owner-{group_name}")
    project = make_project(owner, "safe-project")
    client.force_login(actor)

    response = client.delete(f"/api/projects/{project.slug}/")

    assert response.status_code == 200
    assert not Project.objects.filter(pk=project.pk).exists()


@pytest.mark.django_db
def test_user_deletion_rules_protect_self_roles_and_last_developer(django_user_model):
    groups = ensure_role_groups()
    developer = django_user_model.objects.create_user(username="developer", is_staff=True)
    groups[DEVELOPER_GROUP].user_set.add(developer)
    manager = django_user_model.objects.create_user(username="manager", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(manager)
    other_manager = django_user_model.objects.create_user(username="other-manager", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(other_manager)
    regular = django_user_model.objects.create_user(username="regular")

    assert can_delete_user(manager, regular) is True
    assert can_delete_user(manager, manager) is False
    assert can_delete_user(manager, other_manager) is False
    assert can_delete_user(manager, developer) is False
    assert can_delete_user(developer, developer) is False
    assert can_delete_user(developer, manager) is True
    assert can_delete_user(developer, regular) is True

    second = django_user_model.objects.create_user(username="second-developer", is_staff=True)
    groups[DEVELOPER_GROUP].user_set.add(second)
    assert can_delete_user(developer, second) is True


@pytest.mark.django_db
def test_manager_cannot_change_roles_or_disable_privileged_users(django_user_model):
    groups = ensure_role_groups()
    manager = django_user_model.objects.create_user(username="role-manager", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(manager)
    other_manager = django_user_model.objects.create_user(username="role-manager-other", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(other_manager)
    developer = django_user_model.objects.create_user(username="role-developer", is_staff=True)
    groups[DEVELOPER_GROUP].user_set.add(developer)
    regular = django_user_model.objects.create_user(username="role-regular")

    assert can_change_user_role(manager, regular) is False
    assert can_change_user_role(manager, other_manager) is False
    assert can_change_user_role(manager, developer) is False
    assert can_change_user_status(manager, regular, is_active=False) is True
    assert can_change_user_status(manager, other_manager, is_active=False) is False
    assert can_change_user_status(manager, developer, is_active=False) is False
    assert can_change_user_status(manager, manager, is_active=False) is False


@pytest.mark.django_db
def test_developer_can_manage_other_roles_but_not_self_or_last_developer(django_user_model):
    groups = ensure_role_groups()
    developer = django_user_model.objects.create_user(username="developer-manager", is_staff=True)
    groups[DEVELOPER_GROUP].user_set.add(developer)
    regular = django_user_model.objects.create_user(username="developer-regular")
    manager = django_user_model.objects.create_user(username="developer-manager-target", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(manager)
    lone_target = django_user_model.objects.create_user(username="lone-target", is_staff=True)
    groups[DEVELOPER_GROUP].user_set.add(lone_target)
    developer.is_active = False
    developer.save(update_fields=["is_active"])

    assert can_change_user_role(developer, regular) is True
    assert can_change_user_role(developer, manager) is True
    assert can_change_user_role(developer, developer) is False
    assert can_change_user_status(developer, regular, is_active=False) is True
    assert can_change_user_status(developer, manager, is_active=False) is True
    assert can_change_user_status(developer, developer, is_active=False) is False
    assert is_last_active_developer(lone_target) is True
    assert can_change_user_status(developer, lone_target, is_active=False) is False


@pytest.mark.django_db
def test_user_admin_protects_sensitive_fields_for_manager_and_self(django_user_model):
    groups = ensure_role_groups()
    manager = django_user_model.objects.create_user(username="admin-manager", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(manager)
    developer = django_user_model.objects.create_user(username="admin-developer", is_staff=True)
    groups[DEVELOPER_GROUP].user_set.add(developer)
    regular = django_user_model.objects.create_user(username="admin-regular")
    user_admin = admin.site._registry[django_user_model]
    request_factory = RequestFactory()

    manager_request = request_factory.get("/admin/")
    manager_request.user = manager
    manager_readonly = set(user_admin.get_readonly_fields(manager_request, regular))
    assert {"is_staff", "is_superuser", "groups", "user_permissions"}.issubset(manager_readonly)
    assert "is_active" not in manager_readonly

    self_request = request_factory.get("/admin/")
    self_request.user = developer
    self_readonly = set(user_admin.get_readonly_fields(self_request, developer))
    assert {"is_staff", "is_superuser", "groups", "user_permissions", "is_active"}.issubset(self_readonly)


@pytest.mark.django_db
def test_user_admin_ignores_manager_submitted_role_fields(django_user_model):
    groups = ensure_role_groups()
    manager = django_user_model.objects.create_user(username="post-manager", is_staff=True)
    groups[MANAGER_GROUP].user_set.add(manager)
    regular = django_user_model.objects.create_user(username="post-regular")
    user_admin = admin.site._registry[django_user_model]
    request = RequestFactory().post("/admin/")
    request.user = manager
    candidate = django_user_model.objects.get(pk=regular.pk)
    candidate.is_staff = True
    candidate.is_superuser = True
    candidate.is_active = False
    form = SimpleNamespace(cleaned_data={"is_active": False, "is_staff": True, "is_superuser": True}, instance=candidate)

    user_admin.save_model(request, candidate, form, change=True)
    candidate.refresh_from_db()

    assert candidate.is_staff is False
    assert candidate.is_superuser is False
    assert candidate.is_active is False


@pytest.mark.django_db
def test_deleting_user_preserves_owned_records_with_set_null(django_user_model):
    user = django_user_model.objects.create_user(username="detached-owner")
    project = make_project(user, "detached-project")
    plan = Plan.objects.create(project=project, name="Detached", created_by=user, updated_by=user)
    version = PlanVersion.objects.create(plan=plan, version=1, name=plan.name, status="draft", geometry={}, created_by=user)

    user.delete()

    project.refresh_from_db()
    plan.refresh_from_db()
    version.refresh_from_db()
    assert project.owner_id is None
    assert plan.created_by_id is None
    assert plan.updated_by_id is None
    assert version.created_by_id is None
