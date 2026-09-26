from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from core.permissions import is_manager, manager_required
from core.timeutils import business_today, parse_local_datetime, to_local_input
from staff.models import Employee

from . import services
from .models import Shift


def clock(request):
    me = request.user
    shift = services.open_shift(me)
    context = {"shift": shift}
    if shift:
        breaks = list(shift.breaks.all())
        current_break = next((b for b in breaks if b.end_at is None), None)
        context.update(
            breaks=breaks, current_break=current_break, break_seconds=services.break_seconds(shift, breaks),
            provisional=services.provisional_seconds(shift, breaks=breaks),
        )
    last = Shift.objects.filter(employee=me, status=Shift.Status.CLOSED).order_by("-clock_out_at").first()
    if last:
        context["last"] = {
            "shift": last, "break_seconds": services.break_seconds(last), "worked": services.worked_seconds(last),
        }
    return render(request, "timeclock/clock.html", context)


ACTIONS = {
    "clock_in": (services.clock_in, gettext_lazy("Clocked in.")),
    "start_break": (services.start_break, gettext_lazy("Break started.")),
    "end_break": (services.end_break, gettext_lazy("Break ended.")),
    "clock_out": (services.clock_out, gettext_lazy("Clocked out. Have a good rest of your day.")),
}


@require_POST
def clock_action(request):
    action = request.POST.get("action")
    if action not in ACTIONS:
        raise Http404
    func, success = ACTIONS[action]
    try:
        result = func(request.user)  # always the signed-in person; no employee id is accepted
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        if action == "start_break" and not result[1]:
            messages.info(request, _("Your break was already running."))
        else:
            messages.success(request, str(success))
    return redirect("timeclock:clock")


def _date_range(request, restaurant, default_days=14):
    today = business_today(restaurant)
    date_to = parse_date(request.GET.get("to", "") or "") or today
    date_from = parse_date(request.GET.get("from", "") or "") or (date_to - timedelta(days=default_days - 1))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def mine(request):
    restaurant = request.user.restaurant
    date_from, date_to = _date_range(request, restaurant)
    qs = services.shifts_in_range(
        Shift.objects.filter(employee=request.user).prefetch_related("breaks"), date_from, date_to, restaurant.tz
    ).order_by("clock_in_at")
    rows, total, open_count = services.summarize(qs)
    return render(
        request, "timeclock/mine.html",
        {"rows": rows, "total": total, "open_count": open_count, "date_from": date_from, "date_to": date_to},
    )


@manager_required
def timesheets(request):
    restaurant = request.user.restaurant
    date_from, date_to = _date_range(request, restaurant)
    qs = Shift.objects.filter(restaurant=restaurant).select_related("employee").prefetch_related("breaks")
    employee_id = request.GET.get("employee", "")
    if employee_id.isdigit():
        qs = qs.filter(employee_id=int(employee_id))
    qs = services.shifts_in_range(qs, date_from, date_to, restaurant.tz).order_by("employee__display_name", "clock_in_at")
    rows, total, open_count = services.summarize(qs)
    per_employee = {}
    for row in rows:
        emp = row["shift"].employee
        entry = per_employee.setdefault(emp.pk, {"employee": emp, "seconds": 0, "shifts": 0, "open": 0})
        entry["shifts"] += 1
        if row["worked"] is None:
            entry["open"] += 1
        else:
            entry["seconds"] += row["worked"]
    return render(
        request, "timeclock/timesheets.html",
        {
            "rows": rows, "total": total, "open_count": open_count, "date_from": date_from, "date_to": date_to,
            "employee_id": employee_id, "per_employee": per_employee.values(),
            "staff": Employee.objects.filter(restaurant=restaurant).order_by("display_name"),
        },
    )


def _get_shift(request, pk):
    shift = get_object_or_404(Shift.objects.select_related("employee"), pk=pk, restaurant=request.user.restaurant)
    if not is_manager(request.user) and shift.employee_id != request.user.pk:
        raise Http404  # employees cannot read another person's timesheet by guessing an id
    return shift


def shift_detail(request, pk):
    shift = _get_shift(request, pk)
    tz = request.user.restaurant.tz
    breaks = list(shift.breaks.all())
    corrections = [
        {
            "c": c,
            "before": services.parse_snapshot_times(c.before, tz),
            "after": services.parse_snapshot_times(c.after, tz),
        }
        for c in shift.corrections.select_related("changed_by")
    ]
    return render(
        request, "timeclock/shift.html",
        {
            "shift": shift, "breaks": breaks, "break_seconds": services.break_seconds(shift, breaks),
            "worked": services.worked_seconds(shift, breaks),
            "provisional": services.provisional_seconds(shift, breaks=breaks) if shift.is_open else None,
            "events": shift.events.select_related("actor"), "corrections": corrections,
        },
    )


EXTRA_BREAK_ROWS = 2


@manager_required
def correct(request, pk):
    shift = _get_shift(request, pk)
    tz = request.user.restaurant.tz
    existing = list(shift.breaks.all())
    errors = []
    if request.method == "POST":
        data = request.POST
        rows = int(data.get("break_rows", "0") or 0)
        break_values = [
            {"start": data.get(f"break-{i}-start", ""), "end": data.get(f"break-{i}-end", ""),
             "remove": data.get(f"break-{i}-remove") == "on"}
            for i in range(min(rows, 50))
        ]
        form = {"clock_in": data.get("clock_in", ""), "clock_out": data.get("clock_out", ""), "reason": data.get("reason", "")}
        try:
            clock_in_at = parse_local_datetime(form["clock_in"], tz)
            if clock_in_at is None:
                raise ValidationError(_("Enter the clock-in time."))
            clock_out_at = parse_local_datetime(form["clock_out"], tz)
            breaks = []
            for b in break_values:
                if b["remove"] or (not b["start"].strip() and not b["end"].strip()):
                    continue
                start = parse_local_datetime(b["start"], tz)
                if start is None:
                    raise ValidationError(_("Each break needs a start time."))
                breaks.append((start, parse_local_datetime(b["end"], tz)))
            services.correct_shift(
                request.user, shift, clock_in_at=clock_in_at, clock_out_at=clock_out_at,
                breaks=breaks, reason=form["reason"],
            )
        except ValidationError as exc:
            errors = exc.messages
        except PermissionDenied:
            raise
        else:
            messages.success(request, _("Shift corrected. The previous times are kept in the history."))
            return redirect("timeclock:shift", pk=shift.pk)
    else:
        form = {"clock_in": to_local_input(shift.clock_in_at, tz), "clock_out": to_local_input(shift.clock_out_at, tz), "reason": ""}
        break_values = [
            {"start": to_local_input(b.start_at, tz), "end": to_local_input(b.end_at, tz), "remove": False} for b in existing
        ] + [{"start": "", "end": "", "remove": False} for _i in range(EXTRA_BREAK_ROWS)]
    return render(
        request, "timeclock/correct.html",
        {"shift": shift, "form": form, "break_values": break_values, "errors": errors, "tz_name": request.user.restaurant.time_zone},
    )
