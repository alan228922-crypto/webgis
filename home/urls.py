from django.urls import path

from . import views


urlpatterns = [
    path('', views.index, name='index'),
    path('map/', views.map_dashboard, name='map-dashboard'),
    path('maintainer/gis-layers/', views.maintainer_gis_layers, name='maintainer-gis-layers'),
    path('maintainer/api-services/', views.maintainer_api_services, name='maintainer-api-services'),
    path('maintainer/data-tables/', views.maintainer_data_tables, name='maintainer-data-tables'),
    path('maintainer/plans/', views.maintainer_plans, name='maintainer-plans'),
]
