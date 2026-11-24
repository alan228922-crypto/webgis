import json
import sqlite3
from functools import wraps

from django.db import OperationalError, transaction
from django.db.models import Exists, Max, OuterRef, Prefetch, Q
from django.http import JsonResponse
from django.utils import timezone

from .models import Plan, PlanVersion, PlanWorkflowEvent, Project
from .permissions import (
    EDITABLE_DRAFT_STATUSES,
    PUBLISHED_PLAN_STATUSES,
    canonical_plan_status,
    can_create_plan,
    can_delete_plan,
    can_edit_plan,
    can_edit_project,
    can_view_private_plan,
    can_view_project,
    is_manager_or_developer,
    is_plan_archived,
    is_plan_published,
    is_plan_submitted,
    plan_delete_block_reason,
)
from .planning import GEOMETRY_ERRORS, publication_geometry_error, validate_feature_collection
from .services.worksite_publication import geology_source, public_worksite
from .views_common import _api_authentication_required, _error


EVENT = PlanWorkflowEvent.Action
CONTENT_FIELDS = ("name", "purpose", "geometry", "metadata")


def _public_plan_metadata(metadata):
    fields = ("feature_count", "measurement_crs", "measurement_method")
    return {key: metadata[key] for key in fields if key in metadata} if isinstance(metadata, dict) else {}


def _plan_payload(plan, *, include_private=True):
    public = public_worksite(plan)
    details = plan if include_private else public.revision or plan
    status = plan.status if include_private or not public.revision else "published"
    displayed_time = plan.updated_at if include_private or not public.revision else public.published_at
    payload = {
        "id": plan.id,
        "name": details.name,
        "purpose": details.purpose,
        "status": status,
        "version": details.version,
        "geometry": details.geometry,
        "metadata": details.metadata if include_private else _public_plan_metadata(details.metadata),
        "return_reason": plan.return_reason if include_private else None,
        "updated_at": displayed_time.isoformat(),
        "published_snapshot": public.identity if public else None,
    }
    if include_private:
        payload["lock_version"] = plan.lock_version
    return payload


def _visible_plans(user, project):
    plans = Plan.objects.filter(project=project).select_related(
        "project", "publication_event__revision",
    ).annotate(_has_review_history=Exists(
        PlanWorkflowEvent.objects.filter(plan_id=OuterRef("pk")).exclude(action=EVENT.CREATED)
    )).prefetch_related(Prefetch(
        "versions",
        queryset=PlanVersion.objects.filter(status__in=PUBLISHED_PLAN_STATUSES).order_by("-version", "-id"),
        to_attr="_published_versions",
    ))
    if can_edit_project(user, project):
        return plans
    legacy = PlanVersion.objects.filter(plan_id=OuterRef("pk"), status__in=PUBLISHED_PLAN_STATUSES)
    return plans.filter(
        Q(created_by_id=user.id) | Q(status__in=PUBLISHED_PLAN_STATUSES)
        | Q(publication_event__isnull=False) | Q(Exists(legacy)),
    )


def _validated_plan_values(payload, current=None, project=None):
    values = {
        "name": payload.get("name", current.name if current else ""),
        "purpose": payload.get("purpose", current.purpose if current else ""),
        "geometry": payload.get("geometry", current.geometry if current else {}),
        "metadata": payload.get("metadata", current.metadata if current else {}),
    }
    if not isinstance(values["name"], str) or not values["name"].strip():
        return None, "name is required"
    if not isinstance(values["metadata"], dict):
        return None, "metadata must be a JSON object"
    if values["geometry"]:
        try:
            checked = validate_feature_collection(values["geometry"], project.measurement_crs if project else "EPSG:3826")
        except GEOMETRY_ERRORS as exc:
            return None, str(exc)
        values["geometry"] = checked["geometry"]
        values["metadata"] = {**values["metadata"], "feature_count": checked["feature_count"], "measurement_crs": checked["measurement_crs"], "measurement_method": checked["measurement_method"]}
    values["name"] = values["name"].strip()
    return values, None


def _create_revision(plan, user, change_summary=""):
    return PlanVersion.objects.create(
        plan=plan, version=plan.version, created_by=user, status=plan.status,
        change_summary=change_summary, **{field: getattr(plan, field) for field in CONTENT_FIELDS},
    )


def _current_revision(plan, user):
    revision = plan.versions.filter(version=plan.version).first()
    if revision is None:
        # Existing pre-Phase-2 records may lack a snapshot for their current content.
        return _create_revision(plan, plan.updated_by or plan.created_by or user)
    if any(getattr(revision, field) != getattr(plan, field) for field in CONTENT_FIELDS):
        raise PlanConflict("stored revision differs from current content; save a new revision first")
    return revision


def _event(plan, revision, user, action, reason=""):
    return PlanWorkflowEvent.objects.create(
        plan=plan, revision=revision, actor=user, actor_label=user.get_username(),
        action=action, reason=reason or "",
    )


class PlanConflict(Exception):
    pass


def _plan_transaction(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            with transaction.atomic():
                return view(*args, **kwargs)
        except PlanConflict as exc:
            return _error(str(exc), 409)
        except OperationalError as exc:
            code = getattr(exc.__cause__, "sqlite_errorcode", None)
            if code is None or (code & 0xFF) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            response = _error("database temporarily busy; reload before retrying", 503)
            response["Retry-After"] = "1"
            return response
    return wrapped


def _expected_write(request, plan):
    token = request.headers.get("X-WORKSITE-Lock-Version")
    if token is None:
        return _error("X-WORKSITE-Lock-Version is required", 428)
    if not token.isascii() or not token.isdecimal() or len(token) > 19 or not 1 <= int(token) <= 2**63 - 1:
        return _error("invalid X-WORKSITE-Lock-Version", 400)
    if int(token) != plan.lock_version:
        raise PlanConflict("plan changed; reload before retrying")
    return None


def _write_plan(plan, user, **changes):
    changes.update(lock_version=plan.lock_version + 1, updated_by=user, updated_at=timezone.now())
    applied = Plan.objects.filter(pk=plan.pk, lock_version=plan.lock_version, status=plan.status).update(**changes)
    if not applied:
        raise PlanConflict("plan changed; reload before retrying")
    for field, value in changes.items():
        setattr(plan, field, value)


def _pin_legacy_source(project):
    # Retain the existing unique legacy choice before starting a new publication.
    if project.geology_source_plan_id is None:
        source, _ = geology_source(project)
        if source is not None:
            project.geology_source_plan_id = source.plan_id
            project.save(update_fields=["geology_source_plan"])


def _advance_geology_generation(project):
    project.geology_generation += 1
    project.save(update_fields=["geology_generation"])


def project_plans(request, slug):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if not can_view_project(request.user, project):
        return _error("project not found", 404)
    if request.method == "GET":
        plans = []
        for plan in _visible_plans(request.user, project):
            private = can_view_private_plan(request.user, plan)
            if private or public_worksite(plan):
                plans.append(_plan_payload(plan, include_private=private))
        return JsonResponse({"project": project.slug, "plans": plans})
    if request.method != "POST":
        return _error("GET or POST required", 405)
    if not can_create_plan(request.user, project):
        return _error("project access forbidden", 403)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _error("request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error("request body must be a JSON object")
    if canonical_plan_status(payload.get("status", "draft")) != "draft":
        return _error("new plans must start as draft")
    values, error = _validated_plan_values(payload, project=project)
    if error:
        return _error(error)
    with transaction.atomic():
        plan = Plan.objects.create(
            project=project, version=1, status="draft",
            created_by=request.user, updated_by=request.user, **values,
        )
        summary = str(payload.get("change_summary", ""))[:500]
        revision = _create_revision(plan, request.user, summary)
        _event(plan, revision, request.user, EVENT.CREATED, summary)
        project.setup_status = "saved"
        project.save(update_fields=["setup_status", "updated_at"])
    return JsonResponse({"project": project.slug, "plan": _plan_payload(plan)}, status=201)


def project_plans_ui_metadata(request, slug):
    """Return per-user capabilities without changing the legacy plans API."""
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if not can_view_project(request.user, project):
        return _error("project not found", 404)

    manager_or_developer = is_manager_or_developer(request.user)
    plans = _visible_plans(request.user, project)
    payload = {
        "project": {
            "slug": project.slug,
            "can_create_plan": can_create_plan(request.user, project),
        },
        "plans": [],
    }
    for plan in plans:
        private = can_view_private_plan(request.user, plan)
        public = public_worksite(plan)
        if not private and not public:
            continue
        if not private:
            payload["plans"].append({
                "id": plan.id,
                "status": "published" if public.revision else plan.status,
                "capabilities": {key: key in {"can_view_versions", "can_export"} for key in (
                    "can_edit", "can_submit", "can_review", "can_publish", "can_archive",
                    "can_delete", "can_select_geology_source", "can_view_versions", "can_export",
                )},
            })
            continue
        submitted = is_plan_submitted(plan.status)
        payload["plans"].append({
            "id": plan.id,
            "status": plan.status,
            "capabilities": {
                "can_edit": can_edit_plan(request.user, plan),
                "can_submit": bool(
                    canonical_plan_status(plan.status) in EDITABLE_DRAFT_STATUSES
                    and (manager_or_developer or plan.created_by_id == request.user.id)
                ),
                "can_review": manager_or_developer and submitted,
                "can_publish": manager_or_developer and submitted,
                "can_archive": manager_or_developer and not is_plan_archived(plan.status),
                "can_delete": can_delete_plan(request.user, plan),
                "can_select_geology_source": bool(
                    manager_or_developer and public and not is_plan_archived(plan.status)
                ),
                "can_view_versions": True,
                "can_export": True,
            },
        })
    return JsonResponse(payload)


@_plan_transaction
def project_plan_detail(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id, for_update=True)
    if error:
        return error
    if request.method == "DELETE":
        if not can_delete_plan(request.user, plan):
            reason = plan_delete_block_reason(request.user, plan)
            return _error("plan cannot be deleted" if reason != "permission" else "plan access forbidden", 409 if reason != "permission" else 403)
        if error := _expected_write(request, plan):
            return error
        _write_plan(plan, request.user)
        plan.delete()
        return JsonResponse({"deleted": True, "plan": plan_id})
    if request.method not in {"PUT", "PATCH"}:
        return _error("PUT, PATCH or DELETE required", 405)
    if not can_edit_plan(request.user, plan):
        return _error("plan access forbidden", 403)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _error("request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error("request body must be a JSON object")
    if "status" in payload and canonical_plan_status(payload["status"]) != canonical_plan_status(plan.status):
        return _error("use the workflow endpoint to change plan status")
    if error := _expected_write(request, plan):
        return error
    values, error = _validated_plan_values(payload, plan, plan.project)
    if error:
        return _error(error)
    # Normalization also permits a content-preserving update of older WGS84 rows.
    normalized_current, normalization_error = _validated_plan_values({}, plan, plan.project)
    same_content = not normalization_error and values == normalized_current
    if same_content:
        return JsonResponse({"project": slug, "plan": _plan_payload(plan)})
    next_version = max(plan.version, plan.versions.aggregate(last=Max("version"))["last"] or 0) + 1
    if is_plan_published(plan.status):
        _pin_legacy_source(plan.project)
        if plan.publication_event_id is None:
            _current_revision(plan, request.user)
    if is_plan_published(plan.status) or is_plan_submitted(plan.status):
        values.update(status="draft", return_reason=None)
    _write_plan(plan, request.user, version=next_version, **values)
    _create_revision(plan, request.user, str(payload.get("change_summary", ""))[:500])
    plan.project.setup_status = "saved"
    plan.project.save(update_fields=["setup_status", "updated_at"])
    return JsonResponse({"project": slug, "plan": _plan_payload(plan)})


def _visible_plan(request, slug, plan_id, *, for_update=False):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return None, authentication_error
    projects = Project.objects.select_for_update() if for_update else Project.objects.all()
    project = projects.filter(slug=slug).first()
    if project is None or not can_view_project(request.user, project):
        return None, _error("plan not found", 404)
    plans = Plan.objects.select_related("publication_event__revision").filter(project=project)
    if for_update:
        plans = plans.select_for_update(of=("self",))
    plan = plans.filter(pk=plan_id).first()
    if plan is None:
        return None, _error("plan not found", 404)
    plan.project = project
    if not can_view_private_plan(request.user, plan) and not public_worksite(plan):
        return None, _error("plan not found", 404)
    return plan, None


@_plan_transaction
def project_plan_submit(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id, for_update=True)
    if error:
        return error
    if request.method != "POST":
        return _error("POST required", 405)
    if not is_manager_or_developer(request.user) and plan.created_by_id != request.user.id:
        return _error("plan access forbidden", 403)
    if error := _expected_write(request, plan):
        return error
    previous = canonical_plan_status(plan.status)
    if previous not in EDITABLE_DRAFT_STATUSES:
        return _error("only draft plans or plans needing revision can be submitted")
    revision = _current_revision(plan, request.user)
    _write_plan(plan, request.user, status="submitted")
    _event(plan, revision, request.user, EVENT.RESUBMITTED if previous == "needs_revision" else EVENT.SUBMITTED)
    return JsonResponse({"project": slug, "plan": _plan_payload(plan)})


@_plan_transaction
def project_plan_review(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id, for_update=True)
    if error:
        return error
    if request.method != "POST":
        return _error("POST required", 405)
    if not is_manager_or_developer(request.user):
        return _error("staff access required", 403)
    if error := _expected_write(request, plan):
        return error
    if not is_plan_submitted(plan.status):
        return _error("only submitted plans can be reviewed", 400)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _error("request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error("request body must be a JSON object")
    target = canonical_plan_status(payload.get("status", payload.get("decision", "")))
    target = {"publish": "published", "return": "needs_revision"}.get(target, target)
    if target not in {"published", "needs_revision"}:
        return _error("review status is invalid")
    reason = str(payload.get("reason", "")).strip()
    if target == "needs_revision" and not reason:
        return _error("return reason is required")
    if target == "published":
        error = publication_geometry_error(plan.geometry)
        if error:
            return _error(error)
    revision = _current_revision(plan, request.user)
    publication = target == "published"
    if publication:
        _pin_legacy_source(plan.project)
    event = _event(plan, revision, request.user, EVENT.PUBLISHED if publication else EVENT.RETURNED, reason)
    changes = {"status": target, "return_reason": None if publication else reason}
    if publication:
        changes["publication_event"] = event
    _write_plan(plan, request.user, **changes)
    if publication and plan.project.geology_source_plan_id == plan.pk:
        _advance_geology_generation(plan.project)
    return JsonResponse({"project": slug, "plan": _plan_payload(plan)})


@_plan_transaction
def project_plan_archive(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id, for_update=True)
    if error:
        return error
    if request.method != "POST":
        return _error("POST required", 405)
    if not is_manager_or_developer(request.user):
        return _error("staff access required", 403)
    if error := _expected_write(request, plan):
        return error
    if is_plan_archived(plan.status):
        return _error("plan is already archived")
    revision = _current_revision(plan, request.user)
    _pin_legacy_source(plan.project)
    _write_plan(plan, request.user, status="archived")
    _event(plan, revision, request.user, EVENT.ARCHIVED)
    if plan.project.geology_source_plan_id == plan.pk:
        _advance_geology_generation(plan.project)
    return JsonResponse({"project": slug, "plan": _plan_payload(plan)})


@_plan_transaction
def project_plan_select_geology_source(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id, for_update=True)
    if error:
        return error
    if request.method != "POST":
        return _error("POST required", 405)
    if not is_manager_or_developer(request.user):
        return _error("staff access required", 403)
    if error := _expected_write(request, plan):
        return error
    public = public_worksite(plan)
    if not public or is_plan_archived(plan.status):
        return _error("select an active published worksite")
    if publication_geometry_error(public.geometry, require_source=public.status == "verified"):
        return _error("select a worksite with valid published polygon geometry")
    revision = public.revision or _current_revision(plan, request.user)
    previous = plan.project.geology_source_plan_id
    if previous != plan.pk:
        _write_plan(plan, request.user)
        _event(plan, revision, request.user, EVENT.SOURCE_SELECTED, f"Previous source plan: {previous or 'none'}")
        plan.project.geology_source_plan_id = plan.pk
        plan.project.geology_generation += 1
        plan.project.save(update_fields=["geology_source_plan", "geology_generation"])
    return JsonResponse({"project": slug, "plan": _plan_payload(plan)})


def project_plan_export(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id)
    if error:
        return error
    worksite = plan if can_view_private_plan(request.user, plan) else public_worksite(plan)
    response = JsonResponse(worksite.geometry)
    response["Content-Disposition"] = f'attachment; filename="{slug}-plan-{plan_id}-v{worksite.version}.geojson"'
    return response


def project_plan_versions(request, slug, plan_id):
    plan, error = _visible_plan(request, slug, plan_id)
    if error:
        return error
    include_private = can_view_private_plan(request.user, plan)
    event_history = []
    if include_private:
        current = list(plan.versions.select_related("created_by"))
        event_history = list(plan.workflow_events.select_related("revision"))
    else:
        public = public_worksite(plan)
        current = [public.revision] if public.revision else []
    versions = [
        {
            "id": item.id, "version": item.version, "name": item.name,
            "status": item.status if include_private else "published",
            "change_summary": item.change_summary if include_private else "",
            "created_by": item.created_by.get_username() if include_private and item.created_by else None,
            "created_at": item.created_at.isoformat(), "geometry": item.geometry,
            "metadata": item.metadata if include_private else _public_plan_metadata(item.metadata),
        }
        for item in current
    ]
    payload = {"project": slug, "plan_id": plan.id, "versions": versions}
    if event_history:
        payload["events"] = [
            {"id": event.id, "action": event.action, "version": event.revision.version,
             "actor": event.actor_label, "created_at": event.created_at.isoformat(), "reason": event.reason}
            for event in event_history
        ]
    return JsonResponse(payload)
