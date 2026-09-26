from django.urls import path

from . import views

app_name = "staff"

urlpatterns = [
    path("", views.staff_list, name="list"),
    path("new/", views.staff_create, name="create"),
    path("me/", views.my_profile, name="me"),
    path("me/password/", views.change_password, name="change_password"),
    path("<int:pk>/", views.staff_detail, name="detail"),
    path("<int:pk>/edit/", views.staff_edit, name="edit"),
    path("<int:pk>/role/", views.staff_role, name="role"),
    path("<int:pk>/deactivate/", views.staff_deactivate, name="deactivate"),
    path("<int:pk>/reactivate/", views.staff_reactivate, name="reactivate"),
    path("<int:pk>/setup-link/", views.staff_setup_link, name="setup_link"),
]
