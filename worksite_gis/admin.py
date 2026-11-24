from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from .models import Alert, ApiServiceConfig, ApiSnapshot, CctvCamera, CctvImportBatch, ImportRun, LayerCatalog, Plan, PlanVersion, PlanWorkflowEvent, Project, StationSeed
from .permissions import (
    DEVELOPER_GROUP,
    REGULAR_USER_GROUP,
    can_change_user_role,
    can_change_user_status,
    can_delete_plan,
    can_delete_project,
    can_delete_user,
    can_edit_project,
    can_remove_developer_role,
    can_view_project,
    ensure_role_groups,
    is_developer,
    is_manager,
    is_manager_or_developer,
)


def _remove_bulk_delete_action(actions):
    actions.pop("delete_selected", None)
    return actions


class SafeDeleteAdminMixin:
    delete_guard = None
    delete_message = "此資料目前不可刪除。"

    def get_actions(self, request):
        return _remove_bulk_delete_action(super().get_actions(request))

    def has_delete_permission(self, request, obj=None):
        return obj is not None and self.delete_guard(request.user, obj)

    def delete_model(self, request, obj):
        if not self.delete_guard(request.user, obj):
            raise ValidationError(self.delete_message)
        super().delete_model(request, obj)


@admin.register(LayerCatalog)
class LayerCatalogAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "category", "feature_count", "updated_at")
    readonly_fields = [field.name for field in LayerCatalog._meta.fields]


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = ("source", "status", "feature_count", "created_at")
    readonly_fields = [field.name for field in ImportRun._meta.fields]


@admin.register(StationSeed)
class StationSeedAdmin(admin.ModelAdmin):
    list_display = ("kind", "external_id", "name")


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("source", "severity", "title", "occurred_at")


@admin.register(ApiServiceConfig)
class ApiServiceConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "enabled", "last_checked_at")
    list_filter = ("status", "enabled")


@admin.register(Plan)
class PlanAdmin(SafeDeleteAdminMixin, admin.ModelAdmin):
    delete_guard = staticmethod(can_delete_plan)
    delete_message = "此 Plan 具有受保護狀態或歷史版本，無法刪除。"
    list_display = ("name", "project", "status", "version", "updated_at")
    list_filter = ("status", "project")
    readonly_fields = ("workflow_help",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return is_manager_or_developer(request.user) and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="工區工作流")
    def workflow_help(self, obj):
        return format_html('<a href="{}">前往工區規劃管理</a>', reverse("home-maintainer-plans"))


@admin.register(Project)
class ProjectAdmin(SafeDeleteAdminMixin, admin.ModelAdmin):
    delete_guard = staticmethod(can_delete_project)
    delete_message = "此 Project 含有受保護資料或目前不可刪除。"
    list_display = ("slug", "name", "status", "updated_at")
    readonly_fields = ("geology_source_plan",)
    list_filter = ("status",)

    def has_add_permission(self, request):
        return is_manager_or_developer(request.user) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if obj is not None and not (
            can_view_project(request.user, obj) and can_edit_project(request.user, obj)
        ):
            return False
        return super().has_change_permission(request, obj)


@admin.register(PlanVersion)
class PlanVersionAdmin(admin.ModelAdmin):
    list_display = ("plan", "version", "status", "created_at")
    readonly_fields = [field.name for field in PlanVersion._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return is_manager_or_developer(request.user) and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlanWorkflowEvent)
class PlanWorkflowEventAdmin(PlanVersionAdmin):
    list_display = ("plan", "revision", "action", "actor_label", "created_at")
    readonly_fields = [field.name for field in PlanWorkflowEvent._meta.fields]


User = get_user_model()
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class SafeUserAdmin(UserAdmin):
    sensitive_fields = ("is_staff", "is_superuser", "groups", "user_permissions")

    def get_actions(self, request):
        return _remove_bulk_delete_action(super().get_actions(request))

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if is_manager(request.user) or (obj is not None and request.user.pk == obj.pk):
            readonly.extend(self.sensitive_fields)
        if obj is not None and request.user.pk == obj.pk:
            readonly.append("is_active")
        elif obj is not None and is_manager(request.user) and (is_developer(obj) or is_manager(obj)):
            readonly.append("is_active")
        return tuple(dict.fromkeys(readonly))

    def _original_groups(self, obj):
        return tuple(obj.groups.values_list("pk", flat=True))

    def _restore_role_fields(self, obj, original):
        obj.is_staff = original.is_staff
        obj.is_superuser = original.is_superuser

    def save_model(self, request, obj, form, change):
        original = None
        original_groups = ()
        if change and obj.pk:
            original = type(obj).objects.get(pk=obj.pk)
            original_groups = self._original_groups(original)

        if is_manager(request.user):
            if original is not None:
                self._restore_role_fields(obj, original)
                if is_developer(original) or is_manager(original) or request.user.pk == original.pk:
                    obj.is_active = original.is_active
                obj._worksite_protected_groups = original_groups
            else:
                obj.is_staff = False
                obj.is_superuser = False
                obj._worksite_new_regular_user = True
        elif original is not None and request.user.pk == original.pk:
            self._restore_role_fields(obj, original)
            obj.is_active = original.is_active
            obj._worksite_protected_groups = original_groups
        elif original is not None and is_developer(request.user):
            submitted_groups = form.cleaned_data.get("groups")
            if submitted_groups is None:
                group_names = set(original.groups.values_list("name", flat=True))
            else:
                group_names = set(submitted_groups.values_list("name", flat=True))
            proposed_is_developer = bool(form.cleaned_data.get("is_superuser", obj.is_superuser)) or DEVELOPER_GROUP in group_names
            proposed_is_active = bool(form.cleaned_data.get("is_active", obj.is_active))
            if not can_change_user_role(request.user, original) or not can_change_user_status(request.user, original, is_active=proposed_is_active) or not can_remove_developer_role(
                request.user,
                original,
                is_developer_after=proposed_is_developer,
                is_active_after=proposed_is_active,
            ):
                self._restore_role_fields(obj, original)
                obj.is_active = original.is_active
                obj._worksite_protected_groups = original_groups
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        if hasattr(obj, "_worksite_protected_groups"):
            obj.groups.set(obj._worksite_protected_groups)
        elif getattr(obj, "_worksite_new_regular_user", False):
            ensure_role_groups()[REGULAR_USER_GROUP].user_set.add(obj)

    def has_delete_permission(self, request, obj=None):
        return obj is not None and can_delete_user(request.user, obj)

    def delete_model(self, request, obj):
        if not can_delete_user(request.user, obj):
            raise ValidationError("此帳號目前不可刪除。")
        super().delete_model(request, obj)


@admin.register(ApiSnapshot)
class ApiSnapshotAdmin(admin.ModelAdmin):
    list_display = ("service", "kind", "status", "fetched_at", "expires_at")
    readonly_fields = [field.name for field in ApiSnapshot._meta.fields]


@admin.register(CctvCamera)
class CctvCameraAdmin(admin.ModelAdmin):
    list_display = ("provider", "camera_id", "road_name", "location_name", "imported_at")
    search_fields = ("camera_id", "road_name", "location_name")


@admin.register(CctvImportBatch)
class CctvImportBatchAdmin(admin.ModelAdmin):
    list_display = ("source", "source_file", "imported_count", "updated_count", "invalid_count", "imported_at")
    readonly_fields = [field.name for field in CctvImportBatch._meta.fields]
