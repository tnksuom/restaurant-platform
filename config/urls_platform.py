"""URLs for requests that name no organization: the platform's own site and administration."""
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.i18n import set_language

from core import views as core_views
from tenants import platform_views as views

platform_admin = ([
    path("", views.home, name="home"),
    path("organizations/new/", views.organization_create, name="organization_create"),
    path("organizations/<int:pk>/", views.organization_detail, name="organization"),
], "platform")

urlpatterns = [
    path("healthz", core_views.healthz, name="healthz"),
    path("language/", login_not_required(set_language), name="set_language"),
    path("", views.landing, name="landing"),
    path("signup/", views.signup, name="signup"),
    path("find/", views.find_workspace, name="find_workspace"),
    path("platform/login/", views.PlatformLoginView.as_view(), name="login"),
    path("platform/logout/", views.platform_logout, name="logout"),
    path("platform/", include(platform_admin)),
]
