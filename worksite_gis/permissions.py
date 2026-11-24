"""Centralized WORKSITE role and authentication helpers."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q


REGULAR_USER_GROUP = "WORKSITE 一般使用者"
MANAGER_GROUP = "WORKSITE 管理者"
DEVELOPER_GROUP = "WORKSITE 系統開發者"
ROLE_GROUP_NAMES = (REGULAR_USER_GROUP, MANAGER_GROUP, DEVELOPER_GROUP)
PLAN_STATUS_ALIASES = {"active": "draft", "review": "submitted", "approved": "published", "rejected": "needs_revision"}
PUBLISHED_PLAN_STATUSES = ("published", "approved")
EDITABLE_DRAFT_STATUSES = {"draft", "needs_revision"}


def is_authenticated_user(user):
    return bool(getattr(user, "is_authenticated", False))


def _group_names(user):
    if not is_authenticated_user(user):
        return set()
    cached = getattr(user, "_worksite_role_group_names", None)
    if cached is None:
        cached = set(user.groups.filter(name__in=ROLE_GROUP_NAMES).values_list("name", flat=True))
        user._worksite_role_group_names = cached
    return cached


def is_developer(user):
    return is_authenticated_user(user) and (
        bool(getattr(user, "is_superuser", False))
        or DEVELOPER_GROUP in _group_names(user)
    )


def is_manager(user):
    if not is_authenticated_user(user) or is_developer(user):
        return False
    names = _group_names(user)
    if MANAGER_GROUP in names:
        return True
    return not names and bool(getattr(user, "is_staff", False))


def is_regular_user(user):
    if not is_authenticated_user(user) or is_manager(user) or is_developer(user):
        return False
    names = _group_names(user)
    return REGULAR_USER_GROUP in names or not names


def is_manager_or_developer(user):
    return is_manager(user) or is_developer(user)


def can_view_project(user, project):
    if not is_authenticated_user(user):
        return False
    return is_manager_or_developer(user) or project.status != "archived"


def can_edit_project(user, project):
    if not is_authenticated_user(user):
        return False
    return is_manager_or_developer(user) or project.owner_id == user.id


def can_create_plan(user, project):
    return can_edit_project(user, project)


def can_view_plan(user, plan):
    return can_view_project(user, plan.project) and (
        is_plan_published(plan.status) or can_view_private_plan(user, plan) or has_published_versions(plan)
    )


def can_view_private_plan(user, plan):
    return can_view_project(user, plan.project) and (
        can_edit_project(user, plan.project) or plan.created_by_id == user.id
    )


def can_edit_plan(user, plan):
    if not can_view_project(user, plan.project) or is_plan_archived(plan.status):
        return False
    if is_manager_or_developer(user):
        return True
    if plan.created_by_id != user.id:
        return False
    return (
        canonical_plan_status(plan.status) in EDITABLE_DRAFT_STATUSES
        or (plan.project.owner_id == user.id and is_plan_published(plan.status))
    )


def canonical_plan_status(status):
    return PLAN_STATUS_ALIASES.get(status, status) if isinstance(status, str) else None


def is_plan_published(status):
    return canonical_plan_status(status) == "published"


def is_plan_submitted(status):
    return canonical_plan_status(status) == "submitted"


def is_plan_archived(status):
    return canonical_plan_status(status) == "archived"


def has_published_versions(plan):
    """Return whether a plan has history that must never be removed."""

    if plan.publication_event_id is not None:
        return True

    from .models import PlanVersion

    prefetched = getattr(plan, "_published_versions", None)
    if prefetched is not None:
        return bool(prefetched)
    return PlanVersion.objects.filter(plan=plan, status__in=PUBLISHED_PLAN_STATUSES).exists()


def has_referenced_data(obj):
    """Use the current relation graph; unknown objects are protected."""

    if hasattr(obj, "plans"):
        return obj.plans.exists()
    if hasattr(obj, "versions"):
        return has_published_versions(obj)
    return True


def plan_delete_block_reason(user, plan):
    if not is_authenticated_user(user):
        return "authentication_required"
    if not is_manager_or_developer(user) and plan.created_by_id != user.id:
        return "permission"
    if canonical_plan_status(plan.status) in {"submitted", "published", "archived"}:
        return "protected_status"
    if has_published_versions(plan):
        return "published_history"
    reviewed = getattr(plan, "_has_review_history", None)
    if reviewed is None:
        reviewed = plan.workflow_events.exclude(action="created").exists()
    if reviewed:
        return "workflow_history"
    return None


def can_delete_plan(user, plan):
    return plan_delete_block_reason(user, plan) is None


def project_delete_block_reason(user, project):
    if not is_authenticated_user(user):
        return "authentication_required"
    if not is_manager_or_developer(user) and project.owner_id != user.id:
        return "permission"
    if project.status == "archived":
        return "protected_status"
    if any(plan_delete_block_reason(user, plan) for plan in project.plans.all()):
        return "referenced_or_protected_plan"
    return None


def can_delete_project(user, project):
    return project_delete_block_reason(user, project) is None


def can_delete_user(actor, target_user):
    if not is_authenticated_user(actor) or actor.pk == target_user.pk:
        return False
    if is_developer(target_user):
        if not is_developer(actor):
            return False
        user_model = get_user_model()
        active_developers = user_model.objects.filter(
            is_active=True,
            groups__name=DEVELOPER_GROUP,
        ).exclude(pk=target_user.pk)
        if target_user.is_superuser:
            active_developers = active_developers | user_model.objects.filter(
                is_active=True,
                is_superuser=True,
            ).exclude(pk=target_user.pk)
        return active_developers.exists()
    if is_manager(target_user) and not is_developer(actor):
        return False
    return is_manager_or_developer(actor)


def is_last_active_developer(target_user):
    if not getattr(target_user, "is_active", False) or not is_developer(target_user):
        return False
    user_model = get_user_model()
    active_developers = user_model.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name=DEVELOPER_GROUP)
    ).distinct()
    return active_developers.count() <= 1


def can_change_user_role(actor, target_user):
    """Only a developer may change another user's role fields."""

    return is_developer(actor) and actor.pk != target_user.pk


def can_remove_developer_role(actor, target_user, *, is_developer_after, is_active_after):
    if not can_change_user_role(actor, target_user):
        return False
    if is_developer(target_user) and (not is_developer_after or not is_active_after):
        return not is_last_active_developer(target_user)
    return True


def can_change_user_status(actor, target_user, *, is_active):
    """Protect self, privileged users, and the final active developer."""

    if not is_authenticated_user(actor) or actor.pk == target_user.pk:
        return False
    if is_manager(actor):
        return is_regular_user(target_user)
    if not is_developer(actor):
        return False
    if is_developer(target_user) and not is_active:
        return not is_last_active_developer(target_user)
    return True


def ensure_role_groups(using=None):
    manager = Group.objects.using(using)
    groups = {}
    for name in ROLE_GROUP_NAMES:
        groups[name], _ = manager.get_or_create(name=name)
    return groups


def _model_permissions(app_label, model, actions, using=None):
    content_type = ContentType.objects.using(using).get(app_label=app_label, model=model)
    return Permission.objects.using(using).filter(
        content_type=content_type,
        codename__in={f"{action}_{model}" for action in actions},
    )


def ensure_role_permissions(using=None):
    """Idempotently connect WORKSITE role groups to coarse Django Admin permissions."""

    groups = ensure_role_groups(using=using)
    regular_group = groups[REGULAR_USER_GROUP]
    manager_group = groups[MANAGER_GROUP]
    developer_group = groups[DEVELOPER_GROUP]

    # Regular users are front-end/API users, never Django Admin users.
    regular_group.permissions.clear()

    manager_group.permissions.clear()
    manager_permissions = []
    manager_permissions.extend(_model_permissions("auth", "user", {"add", "change", "delete", "view"}, using))
    for model in ("project", "plan", "planversion"):
        manager_permissions.extend(_model_permissions("worksite_gis", model, {"add", "change", "delete", "view"}, using))
    manager_group.permissions.add(*manager_permissions)
    manager_group.permissions.remove(*Permission.objects.using(using).filter(content_type__app_label="auth", content_type__model="group"))

    developer_permissions = Permission.objects.using(using).filter(
        Q(content_type__app_label="worksite_gis")
        | Q(content_type__app_label="auth", content_type__model__in={"user", "group"})
    )
    developer_group.permissions.add(*developer_permissions)
    return groups


def sync_existing_user_roles(using=None):
    """Map users without an existing WORKSITE role group, without removing roles."""

    groups = ensure_role_groups(using=using)
    user_model = get_user_model()
    users = user_model.objects.using(using).all()
    managed_names = set(ROLE_GROUP_NAMES)
    for user in users:
        existing = set(user.groups.using(using).filter(name__in=managed_names).values_list("name", flat=True))
        if existing:
            continue
        if user.is_superuser:
            target = DEVELOPER_GROUP
        elif user.is_staff:
            target = MANAGER_GROUP
        else:
            target = REGULAR_USER_GROUP
        groups[target].user_set.add(user)
    mapped_users = sum(
        groups[name].user_set.count() for name in ROLE_GROUP_NAMES
    )
    return {"groups": tuple(ROLE_GROUP_NAMES), "mapped_users": mapped_users}
