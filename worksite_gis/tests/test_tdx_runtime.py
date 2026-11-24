from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
from django.utils import timezone

from worksite_gis.integrations.base import SnapshotCache
from worksite_gis.integrations.tdx import TdxClient
from worksite_gis.models import ApiSnapshot, Project


def test_no_credentials_or_demo_precedes_all_tdx_http(settings):
    mock = Mock()
    adapter = TdxClient("", "", client=mock)
    for name in ("fetch_cctv", "fetch_train_live", "fetch_highway_live"):
        assert getattr(adapter, name)().reason == "not_configured"
    with pytest.raises(httpx.HTTPError, match="not_configured"):
        adapter.fetch_highway_section_shapes()
    settings.WORKSITE_DEMO = True
    assert TdxClient("example", "synthetic", client=mock).fetch_cctv().reason == "disabled"
    assert not mock.mock_calls


@pytest.mark.parametrize("http_status,reason", [(401, "token_rejected"), (403, "forbidden"), (429, "rate_limited"), (503, "upstream_unavailable")])
def test_tdx_status_with_bounded_retry_after(http_status, reason):
    requests = []

    def upstream(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 60})
        return httpx.Response(http_status, headers={"Retry-After": "999999"})

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        result = TdxClient("example", "synthetic", client=client).fetch_cctv()
    assert result.reason == reason
    assert result.retry_after_seconds == (300 if http_status == 429 else None)
    assert len(requests) == 2
    assert "synthetic" not in result.error


def test_timeout_and_invalid_response_differ_from_empty():
    def timeout(request):
        raise httpx.ReadTimeout("synthetic secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        result = TdxClient("example", "synthetic", client=client).fetch_train_live()
    assert result.reason == "timeout"
    assert "secret" not in result.error

    def upstream(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        return httpx.Response(200, json={"Unexpected": []})

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        assert TdxClient("example", "synthetic", client=client).fetch_cctv().reason == "invalid_response"


@pytest.mark.django_db
def test_tdx_bounded_stale_and_demo_cannot_reuse_production_cache(client, settings):
    now = timezone.now()
    def snapshot(age):
        return ApiSnapshot.objects.create(service="tdx", kind="cctv", fetched_at=now-timedelta(seconds=age),
                                          expires_at=now-timedelta(seconds=1), payload=[{"synthetic": True}])
    old = snapshot(900)
    def timeout(request):
        raise httpx.ConnectTimeout("synthetic")
    with httpx.Client(transport=httpx.MockTransport(timeout)) as mock:
        adapter = TdxClient("example", "synthetic", client=mock)
        adapter.kind = "cctv"
        assert SnapshotCache().fetch(adapter).runtime_state == "unavailable"
        old.delete()
        snapshot(90)
        assert SnapshotCache().fetch(adapter).runtime_state == "stale"
        settings.WORKSITE_DEMO = True
        assert SnapshotCache().fetch(adapter).runtime_state == "disabled"

    project = Project.objects.create(slug="demo", name="Synthetic", search_center_latitude=25, search_center_longitude=121)
    assert client.get(f"/api/projects/{project.slug}/nearby/cctv/").json()["runtime_state"] == "disabled"
