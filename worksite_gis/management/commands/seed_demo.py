"""Idempotent, local-only Synthetic Data initialization."""

import json
import os
from secrets import token_urlsafe

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client
from django.urls import reverse

from config.demo_paths import ACCOUNTS_FILE, DATABASE_FILE, validate_demo_directory
from worksite_gis.demo import DEMO_PROJECT_SLUG, demo_fixtures
from worksite_gis.models import Project
from worksite_gis.permissions import MANAGER_GROUP, REGULAR_USER_GROUP


ACCOUNTS = {"demo-owner": REGULAR_USER_GROUP, "demo-reviewer": MANAGER_GROUP, "demo-reader": REGULAR_USER_GROUP}


def _credentials(directory, creating):
    path = directory / ACCOUNTS_FILE
    if path.exists():
        credentials = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(credentials, dict) or credentials.keys() != ACCOUNTS.keys() or not all(
            isinstance(password, str) and len(password) >= 24 for password in credentials.values()
        ):
            raise CommandError("Invalid Demo account file")
        return credentials
    if not creating:
        raise CommandError("Demo account file is missing; use the existing file or initialize a fresh workspace")
    credentials = {username: token_urlsafe(24) for username in ACCOUNTS}
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as account_file:
        json.dump(credentials, account_file, indent=2)
        account_file.write("\n")
    return credentials


def _response(response, expected_status):
    if response.status_code != expected_status:
        raise CommandError(f"Synthetic workflow initialization failed (HTTP {response.status_code})")
    return response.json()


def _seed_worksite(project, owner, reviewer):
    fixture, _ = demo_fixtures()
    owner_client, reviewer_client = (Client(HTTP_HOST="127.0.0.1") for _ in range(2))
    owner_client.force_login(owner)
    reviewer_client.force_login(reviewer)
    request_payload = {"name": "Synthetic worksite", "geometry": fixture["worksite"], "metadata": {"synthetic": True}}
    path = reverse("project-plans", kwargs={"slug": project.slug})
    plan = _response(owner_client.post(path, request_payload, content_type="application/json"), 201)["plan"]

    def transition(client, route, plan, payload=None):
        path = reverse(route, kwargs={"slug": project.slug, "plan_id": plan["id"]})
        return _response(client.post(path, payload or {}, content_type="application/json",
                                     headers={"X-WORKSITE-Lock-Version": str(plan["lock_version"])}), 200)["plan"]

    plan = transition(owner_client, "project-plan-submit", plan)
    plan = transition(reviewer_client, "project-plan-review", plan, {"status": "published"})
    transition(reviewer_client, "project-plan-geology-source", plan)
    owner_client.logout()
    reviewer_client.logout()


class Command(BaseCommand):
    help = "Initialize the isolated Synthetic Data workspace created by demo.py."

    def handle(self, *args, **options):
        if not getattr(settings, "WORKSITE_DEMO", False):
            raise CommandError("Demo seed requires demo.py and isolated Demo settings")
        directory = validate_demo_directory(settings.WORKSITE_DEMO_DIR)
        database = settings.DATABASES["default"]
        if database["ENGINE"] != "django.db.backends.sqlite3" or database["NAME"] != directory / DATABASE_FILE:
            raise CommandError("Demo seed requires the isolated SQLite database")

        with transaction.atomic():
            model = get_user_model()
            credentials = _credentials(directory, not model.objects.filter(username__in=ACCOUNTS).exists())
            users = {}
            for username, group_name in ACCOUNTS.items():
                user, created = model.objects.get_or_create(username=username, defaults={"is_staff": group_name == MANAGER_GROUP})
                if created:
                    user.set_password(credentials[username])
                    user.save(update_fields=["password"])
                    user.groups.add(Group.objects.get(name=group_name))
                users[username] = user
            project, created = Project.objects.get_or_create(slug=DEMO_PROJECT_SLUG, defaults={
                "owner": users["demo-owner"], "name": "Synthetic Worksite",
                "center_latitude": 24.9935, "center_longitude": 121.0005, "default_zoom": 15,
                "search_center_latitude": 24.9935, "search_center_longitude": 121.0005,
                "measurement_crs": "EPSG:3826", "setup_status": "located",
            })
            if created:
                _seed_worksite(project, users["demo-owner"], users["demo-reviewer"])
            self.stdout.write("Synthetic Data seed ready.")
