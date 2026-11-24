from datetime import timedelta

import pytest
from django.utils import timezone

from worksite_gis.integrations.base import AdapterResult
from worksite_gis.models import Alert


@pytest.mark.django_db
def test_sync_cwa_alerts_uses_injected_adapter_and_deduplicates():
    from worksite_gis.alert_sync import sync_cwa_alerts

    now = timezone.now()
    record = {
        "external_id": "cwa-service-1",
        "source": "CWA",
        "severity": "red",
        "title": "Heavy rain",
        "payload": {"location": "Taoyuan"},
        "occurred_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }

    class FakeAdapter:
        def __init__(self, api_key, timeout):
            assert api_key == "secret"
            assert timeout == 3

    class FakeCache:
        def __init__(self, ttl):
            assert ttl == 60

        def fetch(self, adapter):
            assert isinstance(adapter, FakeAdapter)
            return AdapterResult("ok", "cwa", now, [record])

    kwargs = {
        "api_key": "secret",
        "timeout": 3,
        "cache_ttl": 60,
        "adapter_class": FakeAdapter,
        "snapshot_cache_class": FakeCache,
        "now": lambda: now,
    }
    assert sync_cwa_alerts(**kwargs) == ("ok", "")
    assert sync_cwa_alerts(**kwargs) == ("ok", "")
    assert Alert.objects.count() == 1
