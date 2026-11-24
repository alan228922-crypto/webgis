import re

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import reverse


@pytest.mark.django_db
def test_login_page_accepts_valid_credentials_and_safe_next(client, django_user_model):
    django_user_model.objects.create_user(username="login-user", password="Valid-pass-123")

    response = client.get(reverse("login"))
    assert response.status_code == 200

    response = client.post(
        reverse("login"),
        {"username": "login-user", "password": "Valid-pass-123", "next": "/map/"},
    )

    assert response.status_code == 302
    assert response["Location"].endswith("/map/")
    assert client.get("/api/projects/").status_code == 200


@pytest.mark.django_db
def test_login_rejects_invalid_and_inactive_accounts(client, django_user_model):
    user = django_user_model.objects.create_user(username="inactive-user", password="Valid-pass-123", is_active=False)

    response = client.post(reverse("login"), {"username": user.username, "password": "Valid-pass-123"})

    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_login_rejects_external_next_url(client, django_user_model):
    django_user_model.objects.create_user(username="safe-next", password="Valid-pass-123")

    response = client.post(
        reverse("login"),
        {"username": "safe-next", "password": "Valid-pass-123", "next": "https://evil.example/"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/"


@pytest.mark.django_db
def test_logout_requires_post_and_invalidates_session(client, django_user_model):
    user = django_user_model.objects.create_user(username="logout-user", password="Valid-pass-123")
    client.force_login(user)

    assert client.get(reverse("logout")).status_code == 405
    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert response["Location"].endswith(reverse("login"))
    assert client.get("/api/projects/").status_code == 401


@pytest.mark.django_db
def test_password_change_updates_only_current_users_password(client, django_user_model):
    user = django_user_model.objects.create_user(username="change-user", password="Old-valid-123")
    client.force_login(user)

    response = client.post(
        reverse("password_change"),
        {"old_password": "Old-valid-123", "new_password1": "New-valid-456", "new_password2": "New-valid-456"},
    )

    assert response.status_code == 302
    assert response["Location"].endswith(reverse("password_change_done"))
    user.refresh_from_db()
    assert user.check_password("New-valid-456")
    assert not user.check_password("Old-valid-123")
    assert client.get("/api/projects/").status_code == 200


@pytest.mark.django_db
def test_password_change_requires_current_password_and_matching_valid_new_password(client, django_user_model):
    user = django_user_model.objects.create_user(username="change-invalid", password="Old-valid-123")
    client.force_login(user)

    wrong_old = client.post(
        reverse("password_change"),
        {"old_password": "wrong", "new_password1": "New-valid-456", "new_password2": "New-valid-456"},
    )
    mismatch = client.post(
        reverse("password_change"),
        {"old_password": "Old-valid-123", "new_password1": "New-valid-456", "new_password2": "Different-789"},
    )
    weak = client.post(
        reverse("password_change"),
        {"old_password": "Old-valid-123", "new_password1": "123", "new_password2": "123"},
    )

    assert wrong_old.status_code == 200
    assert mismatch.status_code == 200
    assert weak.status_code == 200
    user.refresh_from_db()
    assert user.check_password("Old-valid-123")


def test_password_change_requires_login(client):
    response = client.get(reverse("password_change"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_password_reset_is_single_use_and_does_not_leak_unknown_email(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="reset-user",
        email="reset@example.com",
        password="Old-valid-123",
    )

    known = client.post(reverse("password_reset"), {"email": user.email})
    known_body = known.content
    unknown = client.post(reverse("password_reset"), {"email": "missing@example.com"})

    assert known.status_code == unknown.status_code == 302
    assert known_body == unknown.content
    assert len(mail.outbox) == 1

    match = re.search(r"/accounts/reset/([^/]+)/([^/]+)/", mail.outbox[0].body)
    assert match
    reset_url = f"/accounts/reset/{match.group(1)}/{match.group(2)}/"
    reset_confirm = client.get(reset_url)
    assert reset_confirm.status_code == 302
    confirm = client.post(
        reset_confirm["Location"],
        {"new_password1": "Reset-valid-456", "new_password2": "Reset-valid-456"},
    )

    assert confirm.status_code == 302
    user.refresh_from_db()
    assert user.check_password("Reset-valid-456")
    assert client.post(
        reset_confirm["Location"],
        {"new_password1": "Reset-again-789", "new_password2": "Reset-again-789"},
    ).status_code == 200


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PASSWORD_RESET_TIMEOUT=-1)
def test_password_reset_expired_token_is_rejected(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="expired-reset",
        email="expired@example.com",
        password="Old-valid-123",
    )
    client.post(reverse("password_reset"), {"email": user.email})
    match = re.search(r"/accounts/reset/([^/]+)/([^/]+)/", mail.outbox[0].body)

    response = client.get(f"/accounts/reset/{match.group(1)}/{match.group(2)}/")

    assert response.status_code == 200
    assert "無效" in response.content.decode()


@pytest.mark.django_db
def test_base_header_exposes_authentication_actions_without_changing_map(client, django_user_model):
    anonymous = client.get("/")
    assert reverse("login") in anonymous.content.decode()

    user = django_user_model.objects.create_user(username="header-user", password="Valid-pass-123")
    client.force_login(user)
    authenticated = client.get("/")
    body = authenticated.content.decode()

    assert user.username in body
    assert reverse("password_change") in body
    assert 'action="/accounts/logout/"' in body
    assert client.get("/map/").status_code == 200


@pytest.mark.django_db
def test_map_dashboard_requires_login_and_preserves_next(client):
    response = client.get(reverse("map-dashboard"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    assert f"next={reverse('map-dashboard')}" in response["Location"]


@pytest.mark.django_db
def test_authenticated_map_dashboard_keeps_bootstrap_markup(client, django_user_model):
    user = django_user_model.objects.create_user(username="map-dashboard-user", password="Valid-pass-123")
    client.force_login(user)

    response = client.get(reverse("map-dashboard"))

    assert response.status_code == 200
    page = response.content.decode("utf-8")
    for marker in (
        "map.js",
        "dashboard.css",
        'id="map"',
        'id="location-search-form"',
        'id="settings-modal"',
        'id="layer-drawer"',
    ):
        assert marker in page
