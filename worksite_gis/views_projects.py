import json

from django.db import transaction
from django.http import JsonResponse

from .models import Project
from .permissions import (
    can_edit_project,
    can_delete_project,
    can_view_project,
    is_manager_or_developer,
    project_delete_block_reason,
)
from .views_common import _api_authentication_required, _error


def _project_payload(project):
    return {
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "center": {"latitude": project.center_latitude, "longitude": project.center_longitude},
        "default_zoom": project.default_zoom,
        "display_bounds": project.display_bounds,
        "timezone": project.timezone,
        "measurement_crs": project.measurement_crs,
        "setup_status": project.setup_status,
        "status": project.status,
        "metadata": project.metadata,
        "search_center": {
            "label": project.search_center_label,
            "address": project.search_center_address,
            "latitude": project.search_center_latitude,
            "longitude": project.search_center_longitude,
            "radius_km": project.search_radius_km,
            "provider": project.search_center_provider,
            "raw_id": project.search_center_raw_id,
            "precision": project.search_center_precision,
            "searched_at": project.search_center_searched_at.isoformat() if project.search_center_searched_at else None,
        } if project.search_center_latitude is not None else None,
    }


def projects(request):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error
    if request.method == "GET":
        if not Project.objects.exists():
            Project.objects.create(
                slug="worksite",
                name="Worksite GIS — Engineering Geology Screening",
                center_latitude=24.985556,
                center_longitude=121.310278,
                default_zoom=14,
                measurement_crs="EPSG:3826",
            )
        queryset = Project.objects.all()
        if not is_manager_or_developer(request.user):
            queryset = queryset.exclude(status="archived")
        return JsonResponse({"projects": [_project_payload(project) for project in queryset]})
    if request.method != "POST":
        return _error("GET or POST required", 405)
    try:
        payload = json.loads(request.body or "{}")
        slug = str(payload["slug"]).strip()
        name = str(payload["name"]).strip()
        latitude = float(payload.get("center_latitude", 24.985556))
        longitude = float(payload.get("center_longitude", 121.310278))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _error("slug, name and valid coordinates are required")
    if not slug or not name or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return _error("slug, name or coordinates are invalid")
    if Project.objects.filter(slug=slug).exists():
        return _error("project slug already exists", 409)
    project = Project.objects.create(
        slug=slug,
        name=name,
        owner=request.user,
        center_latitude=latitude,
        center_longitude=longitude,
        default_zoom=int(payload.get("default_zoom", 14)),
        measurement_crs=str(payload.get("measurement_crs", "")).strip(),
    )
    return JsonResponse(_project_payload(project), status=201)


def project_detail(request, slug):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if not can_view_project(request.user, project):
        return _error("project not found", 404)
    if request.method == "DELETE":
        if not can_delete_project(request.user, project):
            reason = project_delete_block_reason(request.user, project)
            return _error("project cannot be deleted" if reason != "permission" else "project access forbidden", 409 if reason != "permission" else 403)
        with transaction.atomic():
            project.delete()
        return JsonResponse({"deleted": True, "project": slug})
    if request.method == "GET":
        return JsonResponse(_project_payload(project))
    if request.method not in {"PATCH", "PUT"}:
        return _error("GET, PATCH or PUT required", 405)
    if not can_edit_project(request.user, project):
        return _error("project access forbidden", 403)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _error("request body must be valid JSON")
    for field in ("name", "description", "default_zoom"):
        if field in payload:
            setattr(project, field, payload[field])
    project.save()
    return JsonResponse(_project_payload(project))


def project_archive(request, slug):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error
    if request.method != "POST":
        return _error("POST required", 405)
    if not is_manager_or_developer(request.user):
        return _error("staff access required", 403)
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    project.status = "archived"
    project.save(update_fields=["status", "updated_at"])
    return JsonResponse(_project_payload(project))
