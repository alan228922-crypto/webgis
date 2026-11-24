from dataclasses import dataclass
from datetime import datetime

from worksite_gis.models import PlanVersion, PlanWorkflowEvent
from worksite_gis.permissions import PUBLISHED_PLAN_STATUSES, is_plan_archived, is_plan_published
from worksite_gis.planning import publication_geometry_error


@dataclass(frozen=True)
class PublicWorksite:
    plan_id: int
    version: int
    geometry: dict
    published_at: datetime
    revision: PlanVersion | None = None
    status: str = "legacy_unverified"

    @property
    def identity(self):
        return {"status": self.status, "plan_id": self.plan_id, "version": self.version}


def public_worksite(plan):
    if plan.publication_event_id:
        event = plan.publication_event
        if event.action == PlanWorkflowEvent.Action.PUBLISHED and event.plan_id == plan.pk and event.revision.plan_id == plan.pk:
            revision = event.revision
            return PublicWorksite(plan.pk, revision.version, revision.geometry, event.created_at, revision, "verified")
        return None
    legacy = getattr(plan, "_published_versions", None)
    if legacy is None:
        legacy = plan.versions.filter(status__in=PUBLISHED_PLAN_STATUSES).order_by("-version", "-id")
    if is_plan_published(plan.status):
        matching = next((row for row in legacy if row.version == plan.version and row.geometry == plan.geometry), None)
        return PublicWorksite(plan.pk, plan.version, plan.geometry, matching.created_at if matching else plan.updated_at, matching)
    latest = next(iter(legacy), None)
    if latest is not None:
        return PublicWorksite(plan.pk, latest.version, latest.geometry, latest.created_at, latest)
    return None


def geology_source(project):
    if project is None:
        return None, "no_worksite_geometry"
    if project.geology_source_plan_id:
        candidate = project.geology_source_plan
        if candidate.project_id != project.pk:
            return None, "invalid_source"
        if is_plan_archived(candidate.status):
            return None, "source_archived"
    else:
        candidates = list(project.plans.filter(status__in=PUBLISHED_PLAN_STATUSES).select_related("publication_event__revision").order_by("pk")[:2])
        if len(candidates) > 1:
            return None, "ambiguous_source"
        if not candidates:
            return None, "no_worksite_geometry"
        candidate = candidates[0]
        if candidate.publication_event_id:
            return None, "source_not_selected"

    source = public_worksite(candidate)
    if not source:
        return None, "invalid_source"
    if publication_geometry_error(source.geometry, require_source=source.status == "verified"):
        return None, "invalid_worksite_geometry"
    return source, None
