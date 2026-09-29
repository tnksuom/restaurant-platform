from django.urls import path

from . import views

app_name = "menu"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/", views.category_edit, name="category_edit"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("categories/<int:pk>/move/<str:direction>/", views.category_move, name="category_move"),
    path("dishes/new/", views.item_create, name="item_create"),
    path("dishes/<int:pk>/", views.item_edit, name="item_edit"),
    path("dishes/<int:pk>/availability/", views.item_availability, name="item_availability"),
    path("dishes/<int:pk>/status/", views.item_status, name="item_status"),
    path("dishes/<int:pk>/move/<str:direction>/", views.item_move, name="item_move"),
    path("dishes/<int:pk>/delete/", views.item_delete, name="item_delete"),
]
