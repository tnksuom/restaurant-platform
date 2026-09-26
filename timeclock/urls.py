from django.urls import path

from . import views

app_name = "timeclock"

urlpatterns = [
    path("", views.clock, name="clock"),
    path("action/", views.clock_action, name="action"),
    path("mine/", views.mine, name="mine"),
    path("staff/", views.timesheets, name="timesheets"),
    path("shift/<int:pk>/", views.shift_detail, name="shift"),
    path("shift/<int:pk>/correct/", views.correct, name="correct"),
]
