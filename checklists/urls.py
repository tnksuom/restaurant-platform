from django.urls import path

from . import views

app_name = "checklists"

urlpatterns = [
    path("", views.today, name="today"),
    path("start/", views.start_run, name="start"),
    path("run/<int:pk>/", views.run_detail, name="run"),
    path("run/<int:pk>/task/<int:response_id>/", views.save_response, name="save_response"),
    path("run/<int:pk>/task/<int:response_id>/issue/", views.report_issue, name="report_issue"),
    path("run/<int:pk>/submit/", views.submit_run, name="submit"),
    path("issues/", views.issue_list, name="issues"),
    path("issues/<int:pk>/", views.issue_detail, name="issue"),
    path("photos/<int:pk>/", views.photo, name="photo"),
    path("review/", views.review, name="review"),
    path("history/", views.history, name="history"),
    path("templates/", views.template_list, name="templates"),
    path("templates/new/", views.template_edit, name="template_create"),
    path("templates/<int:pk>/", views.template_edit, name="template_edit"),
    path("templates/<int:pk>/active/", views.template_set_active, name="template_set_active"),
]
