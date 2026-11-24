import os
from pathlib import Path
import subprocess
import sys

from django.core.management.utils import get_random_secret_key


def run_profile(tmp_path, code, **env):
    settings = {
        "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
        "DJANGO_SETTINGS_MODULE": "config.deployment_settings",
        "SECRET_KEY": get_random_secret_key(), "DJANGO_ALLOWED_HOSTS": "localhost",
        "DB_ENGINE": "sqlite", "DB_NAME": str(tmp_path / "app.sqlite3"),
        "WORKSITE_STATIC_ROOT": str(tmp_path / "static"),
        "WORKSITE_DATA_ROOT": str(tmp_path / "gis"),
        **env,
    }
    return subprocess.run([sys.executable, "-B", "-c", code], env=settings, capture_output=True, text=True)


def test_deployment_requires_explicit_secret_and_proxy_trust(tmp_path):
    code = """
from django.conf import settings
assert not settings.DEBUG and settings.SECURE_SSL_REDIRECT
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
assert settings.SECURE_PROXY_SSL_HEADER is None
assert str(settings.STATIC_ROOT).endswith('static')
"""
    assert run_profile(tmp_path, code).returncode == 0
    rejected = run_profile(tmp_path, code, SECRET_KEY="")
    assert rejected.returncode and "SECRET_KEY" in rejected.stderr


def test_collected_static_served_by_wsgihandler_in_deployment(tmp_path):
    result = run_profile(tmp_path, """
import django
django.setup()
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.test import RequestFactory
call_command('collectstatic', interactive=False, verbosity=0)
request = RequestFactory().get('/static/home/js/map.js', secure=True, HTTP_HOST='localhost')
app = get_wsgi_application()
status = []
def start_response(code, headers):
    status.append(code)
response = app(request.environ, start_response)
assert status == ['200 OK'], status
assert b'function currentSlug' in b''.join(response)
response.close()
""")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "static/home/js/map.js").is_file()
    assert not (tmp_path / "static/data").exists()
