import pytest
from django.urls import reverse

from worksite_gis.permissions import DEVELOPER_GROUP, MANAGER_GROUP, ensure_role_groups


def _assign_group(user, group_name):
    ensure_role_groups()[group_name].user_set.add(user)


@pytest.mark.django_db
def test_anonymous_header_only_shows_login(client):
    body = client.get("/").content.decode()

    assert "登入" in body
    assert "登出" not in body
    assert "修改密碼" not in body
    assert "API 維護" not in body
    assert "/admin/" not in body


@pytest.mark.django_db
def test_regular_user_header_shows_safe_account_actions_only(client, django_user_model):
    user = django_user_model.objects.create_user(username="frontend-regular")
    client.force_login(user)

    body = client.get("/").content.decode()

    assert user.username in body
    assert "一般使用者" in body
    assert "修改密碼" in body
    assert "登出" in body
    assert "API 維護" not in body
    assert "/admin/" not in body


@pytest.mark.django_db
def test_manager_header_shows_maintainer_links_but_not_role_admin(client, django_user_model):
    user = django_user_model.objects.create_user(username="frontend-manager", is_staff=True)
    _assign_group(user, MANAGER_GROUP)
    client.force_login(user)

    body = client.get("/").content.decode()

    assert "管理者" in body
    assert "API 維護" in body
    assert reverse("home-maintainer-gis-layers") in body
    assert reverse("home-maintainer-api-services") in body
    assert reverse("home-maintainer-data-tables") in body
    assert reverse("home-maintainer-plans") in body
    assert "角色管理" not in body
    assert 'action="/accounts/logout/"' in body


@pytest.mark.django_db
def test_developer_header_also_shows_existing_admin_entry(client, django_user_model):
    user = django_user_model.objects.create_user(username="frontend-developer", is_staff=True)
    _assign_group(user, DEVELOPER_GROUP)
    client.force_login(user)

    body = client.get("/").content.decode()

    assert "系統開發者" in body
    assert "/admin/" in body
    assert "角色管理" not in body


@pytest.mark.django_db
def test_regular_user_cannot_open_maintainer_page_even_if_url_is_entered(client, django_user_model):
    user = django_user_model.objects.create_user(username="frontend-boundary")
    client.force_login(user)

    response = client.get(reverse("home-maintainer-plans"))

    assert response.status_code == 403
    assert "維護者權限" in response.content.decode()
    assert reverse("index") in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("route_name", [
    "home-maintainer-api-services",
    "home-maintainer-gis-layers",
    "home-maintainer-data-tables",
    "home-maintainer-plans",
])
def test_anonymous_maintainer_page_uses_general_login_and_safe_next(client, route_name):
    response = client.get(reverse(route_name))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    assert f"next={reverse(route_name)}" in response["Location"]


@pytest.mark.django_db
@pytest.mark.parametrize("route_name", [
    "home-maintainer-api-services",
    "home-maintainer-gis-layers",
    "home-maintainer-data-tables",
    "home-maintainer-plans",
])
def test_staff_user_can_open_maintainer_page_and_breadcrumb_links_home(client, django_user_model, route_name):
    user = django_user_model.objects.create_user(username=f"staff-{route_name}", is_staff=True)
    client.force_login(user)

    response = client.get(reverse(route_name))

    assert response.status_code == 200
    body = response.content.decode()
    assert f'href="{reverse("index")}"' in body
    assert "maintainer-breadcrumb" in body


@pytest.mark.django_db
def test_header_keeps_post_csrf_logout_form(client, django_user_model):
    user = django_user_model.objects.create_user(username="frontend-csrf")
    client.force_login(user)

    body = client.get("/").content.decode()

    assert 'method="post"' in body
    assert 'action="/accounts/logout/"' in body
    assert 'name="csrfmiddlewaretoken"' in body


@pytest.mark.django_db
def test_demo_maintainer_uses_synthetic_project(client, django_user_model, settings):
    from worksite_gis.models import Project

    settings.WORKSITE_DEMO = True
    reviewer = django_user_model.objects.create_user(username="synthetic-reviewer", is_staff=True)
    owner = django_user_model.objects.create_user(username="synthetic-owner")
    Project.objects.create(slug="synthetic-added", name="Synthetic Data project", owner=owner)
    client.force_login(reviewer)
    route = reverse("home-maintainer-plans")
    assert b'data-plans-endpoint="/api/projects/synthetic-worksite/plans/"' in client.get(route).content
    assert b'data-plans-endpoint="/api/projects/synthetic-added/plans/"' in client.get(route, {"project": "synthetic-added"}).content
    assert client.get(route, {"project": "unknown"}).status_code == 404
    client.force_login(owner)
    assert client.get(route, {"project": "synthetic-added"}).status_code == 403
