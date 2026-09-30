from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.i18n import set_language

from core import views as core_views
from staff import views as staff_views

urlpatterns = [
    path("healthz", core_views.healthz, name="healthz"),
    # Language choice (EN/FI/VI) is stored in a cookie; works for customers and staff alike.
    path("language/", login_not_required(set_language), name="set_language"),
    # Staff app
    path("app/", core_views.today, name="today"),
    path("login/", staff_views.LoginView.as_view(), name="login"),
    path("logout/", staff_views.logout_view, name="logout"),
    path("set-password/<uidb64>/<token>/", staff_views.set_password, name="set_password"),
    path("staff/", include("staff.urls")),
    path("inventory/", include("inventory.urls")),
    path("checklists/", include("checklists.urls")),
    path("time/", include("timeclock.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("menu-admin/", include("menu.urls")),
    # Public website (EP01/EP02): home, menu, contact, images, promotions admin
    path("", include("website.urls")),
]
