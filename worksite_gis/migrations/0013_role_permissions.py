from django.db import migrations


ROLE_GROUPS = {
    "regular": "WORKSITE 一般使用者",
    "manager": "WORKSITE 管理者",
    "developer": "WORKSITE 系統開發者",
}


def _permissions(Permission, ContentType, db_alias, app_label, model, actions):
    content_type = ContentType.objects.using(db_alias).get(app_label=app_label, model=model)
    return Permission.objects.using(db_alias).filter(
        content_type=content_type,
        codename__in={f"{action}_{model}" for action in actions},
    )


def initialize_role_permissions(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    for app_label in ("auth", "worksite_gis"):
        for model in apps.get_app_config(app_label).get_models():
            content_type, _ = ContentType.objects.using(db_alias).get_or_create(
                app_label=app_label,
                model=model._meta.model_name,
            )
            for action in ("add", "change", "delete", "view"):
                Permission.objects.using(db_alias).get_or_create(
                    content_type=content_type,
                    codename=f"{action}_{model._meta.model_name}",
                    defaults={"name": f"Can {action} {model._meta.verbose_name_raw}"},
                )
    groups = {
        key: Group.objects.using(db_alias).get_or_create(name=name)[0]
        for key, name in ROLE_GROUPS.items()
    }

    groups["regular"].permissions.clear()

    groups["manager"].permissions.clear()
    manager_permissions = []
    manager_permissions.extend(_permissions(Permission, ContentType, db_alias, "auth", "user", {"add", "change", "delete", "view"}))
    for model in ("project", "plan", "planversion"):
        manager_permissions.extend(_permissions(Permission, ContentType, db_alias, "worksite_gis", model, {"add", "change", "delete", "view"}))
    groups["manager"].permissions.add(*manager_permissions)
    group_permissions = Permission.objects.using(db_alias).filter(content_type__app_label="auth", content_type__model="group")
    groups["manager"].permissions.remove(*group_permissions)

    # Build the OR query without relying on a project-specific custom manager.
    developer_permissions = Permission.objects.using(db_alias).filter(content_type__app_label="worksite_gis")
    developer_permissions = developer_permissions | Permission.objects.using(db_alias).filter(
        content_type__app_label="auth",
        content_type__model__in={"user", "group"},
    )
    groups["developer"].permissions.add(*developer_permissions)


class Migration(migrations.Migration):
    dependencies = [("worksite_gis", "0012_plan_workflow")]

    operations = [migrations.RunPython(initialize_role_permissions, migrations.RunPython.noop)]
