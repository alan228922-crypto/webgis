from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import render
from django.urls import reverse

from worksite_gis.demo import DEMO_PROJECT_SLUG
from worksite_gis.models import Project
from worksite_gis.permissions import can_view_project


def maintainer_page_required(view_func):
    """Require authentication and staff status for HTML maintainer pages."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=reverse("login"))
        if not request.user.is_staff:
            return HttpResponseForbidden(
                "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
                "<title>無權限</title></head><body><main>"
                "<h1>無權限</h1><p>此頁面需要維護者權限。</p>"
                f"<a href='{reverse('index')}'>返回首頁</a>"
                "</main></body></html>"
            )
        return view_func(request, *args, **kwargs)

    return wrapped


def index(request):
    return render(request, 'home/index.html')


@login_required
def map_dashboard(request):
    return render(request, 'home/map.html')


@maintainer_page_required
def maintainer_gis_layers(request):
    return render(request, 'home/maintainer_gis_layers.html')


@maintainer_page_required
def maintainer_api_services(request):
    return render(request, 'home/maintainer_api_services.html')


@maintainer_page_required
def maintainer_data_tables(request):
    return render(request, 'home/maintainer_data_tables.html')


@maintainer_page_required
def maintainer_plans(request):
    slug = "worksite"
    if getattr(settings, "WORKSITE_DEMO", False):
        slug = DEMO_PROJECT_SLUG
        if selected_slug := request.GET.get("project"):
            project = Project.objects.filter(slug=selected_slug).first()
            if project is None or not can_view_project(request.user, project):
                raise Http404
            slug = project.slug
    return render(request, 'home/maintainer_plans.html', {
        "maintainer_project_slug": slug,
        "maintainer_plans_endpoint": reverse("project-plans", kwargs={"slug": slug}),
    })
