from django.db.models import F
from django.shortcuts import render

from checklists.models import ChecklistIssue, ChecklistRun, ChecklistTemplate
from core.permissions import is_manager
from core.timeutils import business_today
from inventory.models import InventoryItem
from timeclock import services as time_services
from timeclock.models import Shift


def today(request):
    """Simple landing page linking to today's work. Not the EP07 management dashboard."""
    user = request.user
    restaurant = user.restaurant
    day = business_today(restaurant)
    shift = time_services.open_shift(user)
    on_break = bool(shift and shift.breaks.filter(end_at__isnull=True).exists())
    templates = ChecklistTemplate.objects.filter(restaurant=restaurant, is_active=True)
    runs = ChecklistRun.objects.filter(restaurant=restaurant, business_date=day)
    context = {
        "day": day,
        "shift": shift,
        "on_break": on_break,
        "checklists_expected": templates.count(),
        "checklists_submitted": runs.filter(status=ChecklistRun.Status.SUBMITTED).count(),
        "checklists_in_progress": runs.filter(status=ChecklistRun.Status.IN_PROGRESS).count(),
        "low_stock": InventoryItem.objects.filter(
            restaurant=restaurant, is_active=True, current_quantity__lte=F("minimum_quantity")
        ).count(),
    }
    if is_manager(user):
        context["open_issues"] = ChecklistIssue.objects.filter(restaurant=restaurant, status=ChecklistIssue.Status.OPEN).count()
        context["on_shift"] = Shift.objects.filter(restaurant=restaurant, status=Shift.Status.OPEN).count()
    return render(request, "core/today.html", context)
