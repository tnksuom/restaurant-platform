from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("locations/", views.locations, name="locations"),
    path("locations/switch/", views.switch_location, name="switch_location"),
    path("account/", views.account, name="account"),
]
