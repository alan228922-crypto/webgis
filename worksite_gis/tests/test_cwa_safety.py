import logging
import socket
import ssl
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from worksite_gis.alert_sync import sync_cwa_alerts
from worksite_gis.integrations.base import AdapterResult
from worksite_gis.integrations.cwa import CwaAlertAdapter, CwaRadarAdapter, CwaWeatherAdapter
from worksite_gis.models import Alert, ApiSnapshot


@pytest.fixture(autouse=True)
def prevent_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("real network access attempted by a CWA test")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.mark.parametrize("adapter", [CwaWeatherAdapter, CwaAlertAdapter])
def test_cwa_clients_verify_tls_even_if_legacy_flag_is_false(monkeypatch, settings, adapter):
    settings.CWA_VERIFY_SSL = False
    options = []

    def client(**kwargs):
        options.append(kwargs)
        return object()

    monkeypatch.setattr(httpx, "Client", client)
    adapter("synthetic-key")

    assert options[0]["verify"] is True


def test_missing_ca_bundle_fails_closed(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("invalid CA reached HTTP client"))
    settings.CWA_CA_BUNDLE = str(tmp_path / "absent-ca.pem")

    result = CwaWeatherAdapter("synthetic-key").fetch()

    assert result.status == "error"
    assert result.error == "tls_verification_failed"


@pytest.mark.parametrize("adapter", [CwaWeatherAdapter, CwaAlertAdapter, CwaRadarAdapter])
def test_missing_cwa_credentials_never_send_request(adapter):
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("external request"))) as client:
        result = adapter("", client=client).fetch()

    assert result.status == "error"
    assert result.records == []


def test_cwa_tls_failure_gets_a_specific_category():
    def upstream(request):
        try:
            raise ssl.SSLCertVerificationError("synthetic certificate verification failed")
        except ssl.SSLCertVerificationError as error:
            raise httpx.ConnectError("synthetic connection failed", request=request) from error

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        result = CwaWeatherAdapter("synthetic-key", client=client).fetch()

    assert result.status == "error"
    assert result.error == "tls_verification_failed"


@pytest.mark.django_db
def test_public_weather_response_does_not_disclose_an_upstream_error(client, settings, monkeypatch, caplog):
    def upstream(request):
        return httpx.Response(403)

    settings.CWA_API_KEY = "synthetic-key-never-output"
    caplog.set_level(logging.INFO, logger="httpx")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as mock:
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock)
        response = client.get("/api/weather/")

    assert response.json()["error"] == "upstream_forbidden"
    assert settings.CWA_API_KEY not in response.content.decode()
    assert settings.CWA_API_KEY not in caplog.text


def test_radar_metadata_keeps_image_observation_time_unknown():
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("image downloaded server-side"))) as client:
        result = CwaRadarAdapter("synthetic-key", client=client).fetch()

    assert result.records[0]["updated_at"] is None
    assert result.records[0]["image_status"] == "unverified"
    assert result.fetched_at is not None


@pytest.mark.django_db
def test_current_alerts_is_read_only_and_only_shows_confirmed_valid_cwa_alerts(client, settings, monkeypatch):
    settings.CWA_API_KEY = "synthetic-key"
    now = timezone.now()
    cases = [
        ("Current", now - timedelta(hours=1), now + timedelta(hours=1), now - timedelta(hours=2)),
        ("Expired", now - timedelta(hours=2), now, now - timedelta(hours=3)),
        ("Future", now + timedelta(minutes=1), now + timedelta(hours=2), now - timedelta(hours=1)),
        ("Unknown issue", now - timedelta(hours=1), now + timedelta(hours=1), None),
        ("Unknown end", now - timedelta(hours=1), None, now - timedelta(hours=2)),
    ]
    for title, start, end, issue in cases:
        payload = {
            "source_issued_at": issue and issue.isoformat(),
            "valid_from": start.isoformat(),
            "expires_at": end and end.isoformat(),
        }
        Alert.objects.create(source="CWA", severity="red", title=title, payload=payload, occurred_at=issue or now)

    ApiSnapshot.objects.create(
        service="CWA", kind="alerts", fetched_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=1), status="ok",
    )
    snapshot_before = list(ApiSnapshot.objects.values())
    alerts_before = list(Alert.objects.values())
    monkeypatch.setattr(CwaAlertAdapter, "fetch", lambda self: pytest.fail("GET attempted external sync"))

    for _ in range(2):
        response = client.get("/api/alerts/current/")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert [row["title"] for row in response.json()["alerts"]] == ["Current"]

    assert list(Alert.objects.values()) == alerts_before
    assert list(ApiSnapshot.objects.values()) == snapshot_before
    history = client.get("/api/alerts/history/").json()["alerts"]
    assert next(row for row in history if row["title"] == "Unknown issue")["occurred_at"] is None


def test_cwa_alert_parser_preserves_unknown_issue_time_and_validity():
    payload = {"records": {"locations": [{
        "locationName": "Synthetic city",
        "hazardConditions": [{
            "phenomena": "Synthetic rain",
            "validTime": {"startTime": "2026-11-23T09:00:00+08:00", "endTime": "2026-11-23T12:00:00+08:00"},
        }],
    }]}}

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        result = CwaAlertAdapter("synthetic-key", client=client).fetch()
        repeated = CwaAlertAdapter("synthetic-key", client=client).fetch()

    assert result.records[0]["occurred_at"] is None
    assert result.records[0]["valid_from"] == "2026-11-23T09:00:00+08:00"
    assert result.records[0]["expires_at"] == "2026-11-23T12:00:00+08:00"
    assert result.records[0]["external_id"] == repeated.records[0]["external_id"]


@pytest.mark.parametrize("status", ["error", "stale"])
@pytest.mark.django_db
def test_unavailable_cwa_sync_keeps_existing_rows_unchanged(status):
    now = timezone.now()
    Alert.objects.create(
        source="CWA", severity="red", title="Still valid", occurred_at=now - timedelta(hours=1),
        payload={"external_id": "existing", "expires_at": (now + timedelta(hours=1)).isoformat()},
    )
    before = list(Alert.objects.values())

    class CacheFailure:
        def __init__(self, ttl):
            pass

        def fetch(self, adapter):
            return AdapterResult(status, "CWA", now, [{
                "source": "CWA", "external_id": "existing", "title": "Old version",
                "occurred_at": (now - timedelta(hours=2)).isoformat(),
            }], status == "stale", "upstream_timeout")

    assert sync_cwa_alerts(
        api_key="synthetic-key", timeout=1, snapshot_cache_class=CacheFailure, now=lambda: now,
    ) == (status, "upstream_timeout")
    assert list(Alert.objects.values()) == before


def test_cwa_refuses_plain_http_upstream(settings):
    settings.CWA_WEATHER_URL = "http://example.invalid/observations"
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("untrusted endpoint reached transport"))) as client:
        result = CwaWeatherAdapter("synthetic-key", client=client).fetch()

    assert result.error == "invalid_upstream_url"


@pytest.mark.django_db
def test_current_cwa_alerts_does_not_start_sync_when_snapshot_missing(client, settings, monkeypatch):
    settings.CWA_API_KEY = "synthetic-key"
    monkeypatch.setattr(CwaAlertAdapter, "fetch", lambda self: pytest.fail("GET attempted external sync"))

    response = client.get("/api/alerts/current/")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["alerts"] == []
    assert not ApiSnapshot.objects.exists()
    assert not Alert.objects.exists()
