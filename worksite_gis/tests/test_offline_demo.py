import json
import os
import sqlite3
import subprocess
import sys


def launch(env, *options):
    return subprocess.run(
        [sys.executable, "demo.py", "--init-only", *options],
        env=env, capture_output=True, text=True, timeout=45,
    )


def test_demo_isolated_seed_is_repeatable_and_reuses_workflow(tmp_path):
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    # Inherited operator settings must not be acted on or parsed.
    env.update(DB_ENGINE="postgresql", DB_NAME=str(tmp_path / "not-demo.sqlite3"),
               CWA_API_KEY="test-inherited-placeholder", EXTERNAL_API_TIMEOUT_SECONDS="not-a-number")
    first = launch(env)
    assert first.returncode == 0, first.stderr
    directory, = tmp_path.glob("worksite-demo-*")
    account_file = directory / "accounts.json"
    credentials = json.loads(account_file.read_text(encoding="utf-8"))
    if os.name == "posix":
        assert account_file.stat().st_mode & 0o077 == 0
    assert all(value not in first.stdout for value in credentials.values())
    assert len(set(credentials.values())) == 3
    assert not (tmp_path / "not-demo.sqlite3").exists()

    def seeded():
        with sqlite3.connect(directory / "demo.sqlite3") as connection:
            return (
                connection.execute("SELECT username,password FROM auth_user ORDER BY username").fetchall(),
                connection.execute("SELECT id,geology_source_plan_id FROM worksite_gis_project WHERE slug='synthetic-worksite'").fetchall(),
                connection.execute("SELECT action FROM worksite_gis_planworkflowevent ORDER BY id").fetchall(),
            )

    users, projects, events = seeded()
    assert len(users) == 3
    assert len(projects) == 1 and projects[0][1] is not None
    assert events == [("created",), ("submitted",), ("published",), ("source_selected",)]
    second = launch(env, "--directory", str(directory))
    assert second.returncode == 0, second.stderr
    assert seeded() == (users, projects, events)
    assert json.loads((directory / "accounts.json").read_text(encoding="utf-8")) == credentials

    database = directory / "demo.sqlite3"
    database.rename(directory / "original-demo.sqlite3")
    database.write_bytes(b"foreign file")
    database.chmod(0o600)
    replaced = launch(env, "--directory", str(directory))
    assert replaced.returncode != 0
    assert "Demo workspace" in replaced.stderr
    assert database.read_bytes() == b"foreign file"


def test_demo_refuses_unmarked_directory(tmp_path):
    directory = tmp_path / "worksite-demo-unmarked"
    directory.mkdir()
    database = directory / "demo.sqlite3"
    database.write_bytes(b"foreign file")
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    result = launch(env, "--directory", str(directory))
    assert result.returncode != 0
    assert "Demo workspace" in result.stderr
    assert database.read_bytes() == b"foreign file"


def test_demo_screening_states_and_browser_network_policy(tmp_path):
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    result = launch(env)
    assert result.returncode == 0, result.stderr
    directory, = tmp_path.glob("worksite-demo-*")
    script = """\
import json
from unittest.mock import patch
import django
django.setup()
from django.conf import settings
from django.test import Client, override_settings

accounts = json.loads((settings.WORKSITE_DEMO_DIR / 'accounts.json').read_text())
client = Client(HTTP_HOST='127.0.0.1')
with patch('httpx.Client.request', side_effect=AssertionError('external HTTP')), \\
     patch('requests.Session.request', side_effect=AssertionError('external HTTP')):
    assert client.login(username='demo-reader', password=accounts['demo-reader'])
    page = client.get('/map/')
    assert page.status_code == 200
    assert b'Synthetic Data' in page.content
    assert b'leaflet.js' in page.content and b'https://' not in page.content
    assert "connect-src 'self'" in page['Content-Security-Policy']
    assert client.get('/map/', REMOTE_ADDR='192.0.2.1').status_code == 403
    project = '/api/projects/synthetic-worksite/'
    candidates = client.get(project + 'location-search/', {'query':'合成地址'}).json()['candidates']
    assert candidates[0]['provider'] == 'mock'
    path = project + 'geology/summary/'
    for scenario, retrieval, ratio, cache in (
        ('normal', 'ok', 0.625, 'live'),
        ('unknown', 'ok', None, 'live'),
        ('timeout', 'error', None, None),
        ('stale', 'ok', 0.625, 'stale_cache'),
        ('empty', 'empty', 0.0, 'live'),
    ):
        with override_settings(WORKSITE_DEMO_SCENARIO=scenario):
            res = client.get(path, {'mode':'screening','buffer_m':50})
            assert res.status_code == 200
            payload = res.json()
            layers = payload['layers']
            assert len(layers) == 5
            liquefaction = next(layer for layer in layers if layer['layer']=='liquefaction')
            quality = liquefaction['data_quality']
            assert (quality['retrieval'], liquefaction['screening']['intersection_ratio'], quality['cache']) == (retrieval, ratio, cache), (scenario, liquefaction)
            if ratio == 0.625:
                assert liquefaction['screening']['intersection_area_m2'] == 6250
    with override_settings(WORKSITE_DEMO_SCENARIO='normal'):
        viewport = client.get(project + "geology/drill/", {"mode":"exploration","bbox":"120.9,24.9,121.1,25"}).json()
        assert viewport["data_quality"]["source"] == "Synthetic Data"
    for url in ('/api/weather/', project + 'nearby/weather-stations/', project + 'nearby/cctv/',
                project + 'radar/', project + 'dynamic-flood/', project + 'train-status/',
                project + 'highway-traffic/', '/api/alerts/current/'):
        assert client.get(url).json()['status'] == 'disabled', url
    surveys = client.get(project + 'plans/').json()['plans']
    assert len(surveys) == 1
    plan_url = project + 'plans/' + str(surveys[0]['id']) + '/'
    assert client.get(plan_url + 'export/').status_code == 200
    owner = Client(HTTP_HOST='127.0.0.1')
    reviewer = Client(HTTP_HOST='127.0.0.1')
    assert owner.login(username='demo-owner', password=accounts['demo-owner'])
    assert reviewer.login(username='demo-reviewer', password=accounts['demo-reviewer'])
    public = owner.get(project + 'plans/').json()['plans'][0]
    response = owner.patch(plan_url, {'name': 'Private v2'}, content_type='application/json',
                           headers={'X-WORKSITE-Lock-Version': str(public['lock_version'])})
    assert response.status_code == 200
    draft = response.json()['plan']
    assert draft['version'] == 2 and draft['status'] == 'draft'
    assert client.get(project + 'plans/').json()['plans'][0]['version'] == 1
    assert client.get(path, {'mode':'screening','buffer_m':50}).json()['screening_context']['published_version'] == 1
    response = reviewer.post(plan_url + 'archive/', {}, content_type='application/json',
                             headers={'X-WORKSITE-Lock-Version': str(draft['lock_version'])})
    assert response.status_code == 200, response.content
    archived = client.get(path, {'mode':'screening','buffer_m':50}).json()
    assert archived['status'] == 'unavailable'
"""
    isolated = {
        **env, "DJANGO_SETTINGS_MODULE": "config.demo_settings", "WORKSITE_DEMO_DIR": str(directory),
    }
    probe = subprocess.run(
        [sys.executable, "-c", script], env=isolated, capture_output=True, text=True, timeout=45,
    )
    assert probe.returncode == 0, probe.stderr
