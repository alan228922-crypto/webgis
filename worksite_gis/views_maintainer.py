import json

from django.http import HttpResponse, JsonResponse

from .models import ApiServiceConfig, LayerCatalog
from .views_common import _catalog_path, _error, _layer_health_path, _staff_required


def maintainer_gis_layers(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return _error("staff access required", 403)
    items = []
    for layer in LayerCatalog.objects.all():
        path = _layer_health_path(layer)
        exists = path.exists()
        validation = layer.metadata.get("validation", {}) if isinstance(layer.metadata, dict) else {}
        healthy = exists and validation.get("ok", True)
        items.append({
            "slug": layer.slug,
            "name": layer.name,
            "category": layer.category,
            "format": layer.format,
            "source": layer.source,
            "path": layer.path,
            "crs": layer.crs,
            "feature_count": layer.feature_count,
            "published": healthy,
            "health": "ok" if healthy else ("missing" if not exists else "failed"),
            "updated_at": layer.updated_at.isoformat(),
        })
    return JsonResponse({"layers": items})


def maintainer_api_services(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return _error("staff access required", 403)
    services = []
    for service in ApiServiceConfig.objects.all():
        services.append({
            "name": service.name, "slug": service.slug, "base_url": service.base_url,
            "endpoint": service.endpoint, "auth_env": service.auth_env,
            "status": service.status, "enabled": service.enabled,
            "last_checked_at": service.last_checked_at.isoformat() if service.last_checked_at else None,
            "response_time_ms": service.response_time_ms, "http_status": service.http_status,
            "error_summary": service.error_summary, "update_frequency": service.update_frequency,
            "data_format": service.data_format, "attribution": service.attribution,
        })
    return JsonResponse({"services": services})


def _table_record_count(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    records = payload.get("records", payload) if isinstance(payload, dict) else payload
    return len(records) if isinstance(records, list) else 0


def maintainer_data_tables(request):
    if not _staff_required(request):
        return _error("staff access required", 403)
    tables = []
    for layer in LayerCatalog.objects.filter(format="json"):
        path = _catalog_path(layer.path)
        csv_output = layer.metadata.get("csv_output") if isinstance(layer.metadata, dict) else None
        csv_path = _catalog_path(csv_output) if csv_output else None
        tables.append({
            "slug": layer.slug,
            "name": layer.name,
            "category": layer.category,
            "format": layer.format,
            "path": layer.path,
            "record_count": _table_record_count(path),
            "csv_available": bool(csv_path and csv_path.exists()),
            "published": path.exists(),
        })
    return JsonResponse({"tables": tables})


def maintainer_data_table_detail(request, slug):
    if not _staff_required(request):
        return _error("staff access required", 403)
    try:
        layer = LayerCatalog.objects.get(slug=slug, format="json")
    except LayerCatalog.DoesNotExist:
        return _error("data table not found", 404)
    if request.GET.get("format", "").lower() == "csv":
        csv_output = layer.metadata.get("csv_output") if isinstance(layer.metadata, dict) else None
        csv_path = _catalog_path(csv_output) if csv_output else None
        if not csv_path or not csv_path.exists():
            return _error("csv output missing", 404)
        response = HttpResponse(csv_path.read_bytes(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{slug}.csv"'
        return response
    path = _catalog_path(layer.path)
    if not path.exists():
        return _error("data table output missing", 503)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return _error("data table output unreadable", 503)
    records = payload.get("records", payload) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        records = []
    query = request.GET.get("q", "").strip().casefold()
    if query:
        records = [record for record in records if query in json.dumps(record, ensure_ascii=False).casefold()]
    try:
        limit = min(max(int(request.GET.get("limit", "100")), 1), 1000)
        offset = max(int(request.GET.get("offset", "0")), 0)
    except ValueError:
        return _error("limit and offset must be numeric")
    page = records[offset:offset + limit]
    return JsonResponse({
        "slug": layer.slug,
        "name": layer.name,
        "record_count": len(records),
        "records": page,
        "pagination": {"limit": limit, "offset": offset, "total": len(records), "has_next": offset + limit < len(records)},
    })
