from django.shortcuts import render
from django.utils.dateparse import parse_date

from core.permissions import manager_required

from . import services


@manager_required
def dashboard(request):
    day = parse_date(request.GET.get("date", "") or "")
    board = services.build(request.user, day)
    return render(request, "dashboard/dashboard.html", {"board": board})
