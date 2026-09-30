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
    path("manage/restaurant/", views.restaurant_info, name="info"),
    path("manage/hours/", views.opening_hours, name="hours"),
    path("manage/hours/special/new/", views.special_day_edit, name="special_day_create"),
    path("manage/hours/special/<int:pk>/", views.special_day_edit, name="special_day_edit"),
    path("manage/hours/special/<int:pk>/delete/", views.special_day_delete, name="special_day_delete"),
    path("manage/images/", views.site_images, name="images"),
]
