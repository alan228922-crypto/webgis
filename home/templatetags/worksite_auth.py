from django import template

from worksite_gis.permissions import is_developer, is_manager, is_manager_or_developer, is_regular_user


register = template.Library()


@register.simple_tag
def worksite_role_label(user):
    if is_developer(user):
        return "系統開發者"
    if is_manager(user):
        return "管理者"
    if is_regular_user(user):
        return "一般使用者"
    return ""


@register.simple_tag
def worksite_is_manager_or_developer(user):
    return is_manager_or_developer(user)


@register.simple_tag
def worksite_is_developer(user):
    return is_developer(user)
