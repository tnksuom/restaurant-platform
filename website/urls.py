from django.urls import path

from . import views

app_name = "website"

urlpatterns = [
    path("", views.home, name="home"),
    path("menu/", views.menu, name="menu"),
    path("contact/", views.contact, name="contact"),
    path("images/<str:key>", views.image, name="image"),
    path("manage/promotions/", views.promotion_list, name="promotions"),
    path("manage/promotions/new/", views.promotion_edit, name="promotion_create"),
    path("manage/promotions/<int:pk>/", views.promotion_edit, name="promotion_edit"),
    path("manage/promotions/<int:pk>/delete/", views.promotion_delete, name="promotion_delete"),
]
