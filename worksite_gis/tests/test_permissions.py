import pytest
from django.contrib.auth.models import Permission

from worksite_gis.permissions import (
    DEVELOPER_GROUP,
    MANAGER_GROUP,
    REGULAR_USER_GROUP,
    ensure_role_groups,
    ensure_role_permissions,
    is_authenticated_user,
    is_developer,
    is_manager,
    is_manager_or_developer,
    is_regular_user,
    sync_existing_user_roles,
)


@pytest.mark.django_db
def test_role_helpers_distinguish_groups_and_superuser(django_user_model):
    groups = ensure_role_groups()
    regular = django_user_model.objects.create_user(username="role-regular")
    manager = django_user_model.objects.create_user(username="role-manager")
    developer = django_user_model.objects.create_user(username="role-developer")
    superuser = django_user_model.objects.create_superuser(username="role-superuser", password="secret")
    groups[REGULAR_USER_GROUP].user_set.add(regular)
    groups[MANAGER_GROUP].user_set.add(manager)
    groups[DEVELOPER_GROUP].user_set.add(developer)

    assert is_regular_user(regular)
    assert not is_manager(regular)
    assert not is_developer(regular)
    assert is_manager(manager)
    assert is_manager_or_developer(manager)
    assert not is_developer(manager)
    assert is_developer(developer)
    assert is_manager_or_developer(developer)
    assert is_developer(superuser)
    assert not is_manager(superuser)


@pytest.mark.django_db
def test_role_group_initialization_is_idempotent_and_maps_existing_users(django_user_model):
    regular = django_user_model.objects.create_user(username="map-regular")
    manager = django_user_model.objects.create_user(username="map-manager", is_staff=True)
    developer = django_user_model.objects.create_superuser(username="map-developer", password="secret")

    first = sync_existing_user_roles()
    second = sync_existing_user_roles()
    groups = ensure_role_groups()

    assert first == second
    assert groups[REGULAR_USER_GROUP].user_set.filter(pk=regular.pk).exists()
    assert groups[MANAGER_GROUP].user_set.filter(pk=manager.pk).exists()
    assert groups[DEVELOPER_GROUP].user_set.filter(pk=developer.pk).exists()
    assert groups[REGULAR_USER_GROUP].user_set.count() >= 1
    assert groups[MANAGER_GROUP].user_set.count() >= 1
    assert groups[DEVELOPER_GROUP].user_set.count() >= 1
    assert regular.check_password("!") is False
    assert django_user_model.objects.filter(pk=developer.pk).exists()


@pytest.mark.django_db
def test_anonymous_project_plan_and_version_reads_return_json_401(client):
    response = client.get("/api/projects/")
    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/json")

    from worksite_gis.models import Plan, Project

    project = Project.objects.create(slug="auth-project", name="Auth project")
    plan = Plan.objects.create(project=project, name="Auth plan")

    for url in (
        "/api/projects/auth-project/",
        "/api/projects/auth-project/plans/",
        f"/api/projects/auth-project/plans/{plan.pk}/export/",
        f"/api/projects/auth-project/plans/{plan.pk}/versions/",
    ):
        response = client.get(url)
        assert response.status_code == 401, url
        assert response.headers["Content-Type"].startswith("application/json"), url


@pytest.mark.django_db
def test_authenticated_project_plan_reads_keep_existing_response_schema(client, django_user_model):
    user = django_user_model.objects.create_user(username="auth-reader")
    client.force_login(user)
    from worksite_gis.models import Plan, Project

    project = Project.objects.create(slug="schema-project", name="Schema project")
    plan = Plan.objects.create(project=project, name="Schema plan", created_by=user)

    projects = client.get("/api/projects/")
    plans = client.get("/api/projects/schema-project/plans/")
    versions = client.get(f"/api/projects/schema-project/plans/{plan.pk}/versions/")

    assert projects.status_code == 200
    assert set(projects.json()) == {"projects"}
    assert plans.status_code == 200
    assert set(plans.json()) == {"project", "plans"}
    assert versions.status_code == 200
    assert set(versions.json()) == {"project", "plan_id", "versions"}


@pytest.mark.django_db
def test_authenticated_regular_user_keeps_staff_archive_boundary(client, django_user_model):
    user = django_user_model.objects.create_user(username="regular-writer")
    client.force_login(user)
    client.post(
        "/api/projects/",
        data={"slug": "owned-by-regular", "name": "Owned"},
        content_type="application/json",
    )

    response = client.post("/api/projects/owned-by-regular/archive/")

    assert response.status_code == 403
    assert response.headers["Content-Type"].startswith("application/json")


def test_anonymous_user_has_no_role():
    from django.contrib.auth.models import AnonymousUser

    user = AnonymousUser()
    assert not is_authenticated_user(user)
    assert not is_regular_user(user)
    assert not is_manager(user)
    assert not is_developer(user)
    assert not is_manager_or_developer(user)


@pytest.mark.django_db
def test_role_groups_receive_controlled_admin_permissions_idempotently(django_user_model):
    groups = ensure_role_permissions()
    first_counts = {name: groups[name].permissions.count() for name in groups}
    ensure_role_permissions()
    second_counts = {name: groups[name].permissions.count() for name in groups}

    assert first_counts == second_counts
    assert groups[REGULAR_USER_GROUP].permissions.count() == 0
    assert groups[MANAGER_GROUP].permissions.filter(content_type__app_label="auth", content_type__model="group").count() == 0
    assert groups[MANAGER_GROUP].permissions.filter(codename__in={"view_user", "add_user", "change_user", "delete_user"}).count() == 4
    assert groups[MANAGER_GROUP].permissions.filter(content_type__app_label="worksite_gis", content_type__model__in={"project", "plan", "planversion"}).count() > 0
    assert groups[DEVELOPER_GROUP].permissions.filter(content_type__app_label="auth", content_type__model__in={"user", "group"}).count() == Permission.objects.filter(content_type__app_label="auth", content_type__model__in={"user", "group"}).count()
    assert groups[DEVELOPER_GROUP].permissions.filter(content_type__app_label="worksite_gis").count() == Permission.objects.filter(content_type__app_label="worksite_gis").count()


@pytest.mark.django_db
def test_group_permissions_control_admin_access_without_changing_role_helpers(django_user_model):
    groups = ensure_role_permissions()
    regular = django_user_model.objects.create_user(username="permissions-regular")
    manager = django_user_model.objects.create_user(username="permissions-manager", is_staff=True)
    developer = django_user_model.objects.create_user(username="permissions-developer", is_staff=True)
    groups[REGULAR_USER_GROUP].user_set.add(regular)
    groups[MANAGER_GROUP].user_set.add(manager)
    groups[DEVELOPER_GROUP].user_set.add(developer)

    assert regular.has_perm("auth.view_user") is False
    assert manager.has_perm("auth.view_user") is True
    assert manager.has_perm("auth.add_user") is True
    assert manager.has_perm("auth.delete_user") is True
    assert manager.has_perm("auth.add_group") is False
    assert developer.has_perm("auth.view_user") is True
    assert developer.has_perm("auth.add_group") is True
    assert is_regular_user(regular)
    assert is_manager(manager)
    assert is_developer(developer)


@pytest.mark.django_db
def test_permission_initialization_does_not_remove_existing_multiple_roles(django_user_model):
    groups = ensure_role_groups()
    user = django_user_model.objects.create_user(username="multi-role-permissions")
    groups[REGULAR_USER_GROUP].user_set.add(user)
    groups[MANAGER_GROUP].user_set.add(user)

    ensure_role_permissions()

    assert set(user.groups.filter(name__in=(REGULAR_USER_GROUP, MANAGER_GROUP)).values_list("name", flat=True)) == {REGULAR_USER_GROUP, MANAGER_GROUP}
