from django.conf import settings

import json
import os
import subprocess
import sys


def test_data_paths_are_under_project_root():
    assert settings.DATA_ROOT == settings.BASE_DIR / "data"
    assert settings.DATA_CATALOG_DIR == settings.DATA_ROOT / "catalog"
    assert settings.OFFLINE_TILE_ROOT == settings.BASE_DIR / "地圖圖磚"


def test_production_security_settings_are_environment_configurable():
    environment = os.environ.copy()
    environment.update({
        "SECRET_KEY": "production-secret-key-with-more-than-fifty-unique-characters-1234567890",
        "DJANGO_DEBUG": "false",
        "DJANGO_ALLOWED_HOSTS": "worksite.example.com,internal.example.com",
        "DJANGO_SECURE_SSL_REDIRECT": "true",
        "DJANGO_SESSION_COOKIE_SECURE": "true",
        "DJANGO_CSRF_COOKIE_SECURE": "true",
        "DJANGO_SECURE_HSTS_SECONDS": "31536000",
    })
    command = (
        "import json; from config import settings; "
        "print(json.dumps({'debug': settings.DEBUG, 'hosts': settings.ALLOWED_HOSTS, "
        "'ssl': settings.SECURE_SSL_REDIRECT, 'session': settings.SESSION_COOKIE_SECURE, "
        "'csrf': settings.CSRF_COOKIE_SECURE, 'hsts': settings.SECURE_HSTS_SECONDS}))"
    )

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=settings.BASE_DIR,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "debug": False,
        "hosts": ["worksite.example.com", "internal.example.com"],
        "ssl": True,
        "session": True,
        "csrf": True,
        "hsts": 31536000,
    }
