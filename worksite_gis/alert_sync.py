from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .integrations.base import SnapshotCache
from .integrations.cwa import CwaAlertAdapter
from .models import Alert


def source_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    return parsed if parsed is not None and timezone.is_aware(parsed) else None


def confirmed_cwa_alert(alert, at):
    payload = alert.payload or {}
    issued_at = source_datetime(payload.get("source_issued_at"))
    valid_from = source_datetime(payload.get("valid_from"))
    expires_at = source_datetime(payload.get("expires_at"))
    return bool(issued_at and valid_from and expires_at and issued_at <= at and valid_from <= at < expires_at)


def sync_cwa_alerts(
    *,
    api_key,
    timeout,
    cache_ttl=60,
    adapter_class=CwaAlertAdapter,
    snapshot_cache_class=SnapshotCache,
    alert_model=Alert,
    now=None,
):
    if not api_key:
        return "disabled", "CWA credential missing"

    result = snapshot_cache_class(cache_ttl).fetch(adapter_class(api_key, timeout=timeout))
    if result.status != "ok":
        return result.status, result.error

    checked_at = (now or timezone.now)()
    with transaction.atomic():
        for item in result.records:
            external_id = item.get("external_id")
            if not external_id:
                continue
            issued_at = source_datetime(item.get("occurred_at"))
            valid_from = source_datetime(item.get("valid_from"))
            expires_at = source_datetime(item.get("expires_at"))
            payload = {
                **(item.get("payload") or {}),
                "external_id": external_id,
                "source_issued_at": issued_at.isoformat() if issued_at else None,
                "valid_from": valid_from.isoformat() if valid_from else None,
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
            source = item.get("source", "CWA")
            alert = alert_model.objects.filter(source=source, payload__external_id=external_id).first()
            values = {
                "severity": item.get("severity", "yellow"),
                "title": item.get("title", "CWA 警報"),
                "payload": payload,
                # The existing DB column is non-nullable. The API uses the
                # verified source time from payload for all CWA records.
                "occurred_at": issued_at or (alert.occurred_at if alert else result.fetched_at),
                "cleared_at": checked_at if expires_at and expires_at <= checked_at else None,
            }
            if alert:
                alert_model.objects.filter(pk=alert.pk).update(**values)
            else:
                alert_model.record(source=source, **values)

        for alert in alert_model.objects.filter(source="CWA", cleared_at__isnull=True):
            expires_at = source_datetime((alert.payload or {}).get("expires_at"))
            if expires_at and expires_at <= checked_at:
                alert_model.objects.filter(pk=alert.pk).update(cleared_at=checked_at)
    return result.status, result.error
