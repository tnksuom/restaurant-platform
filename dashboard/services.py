"""Management dashboard (EP07): read-only summaries of today's operation.

Everything here reads data recorded by the EP03–EP06 modules; nothing is stored.
"""
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.db.models import F, Prefetch

from checklists.models import ChecklistIssue, ChecklistResponse, ChecklistRun, ChecklistTemplate, ResponseStatus, ResponseType
from checklists.services import run_counts
from core.permissions import is_manager
from core.timeutils import business_today, day_range_utc, now
from inventory.models import InventoryItem
from timeclock import services as time_services
from timeclock.models import Break, Shift


@dataclass
class Dashboard:
    day: object
    is_today: bool
    generated_at: object
    staff_on_shift: list = field(default_factory=list)
    hours: dict = field(default_factory=dict)
    checklists: dict = field(default_factory=dict)
    incomplete_tasks: list = field(default_factory=list)
    not_started: list = field(default_factory=list)
    open_issues: list = field(default_factory=list)
    resolved_issues: list = field(default_factory=list)
    low_stock: list = field(default_factory=list)
    flagged_temperatures: list = field(default_factory=list)

    @property
    def on_break_count(self):
        return sum(1 for s in self.staff_on_shift if s["on_break"])

    @property
    def attention_count(self):
        """Things a manager should act on; used for the headline."""
        return (
            len(self.open_issues)
            + len(self.low_stock)
            + len(self.flagged_temperatures)
            + self.checklists.get("pending_required", 0)
            + len(self.not_started)
        )


def staff_status(restaurant, at):
    """US-07.05 — everyone clocked in right now, with break state (independent of the chosen date)."""
    shifts = (
        Shift.objects.filter(restaurant=restaurant, status=Shift.Status.OPEN)
        .select_related("employee")
        .prefetch_related(Prefetch("breaks", queryset=Break.objects.order_by("start_at", "id")))
        .order_by("clock_in_at")
    )
    rows = []
    for shift in shifts:
        breaks = list(shift.breaks.all())
        current = next((b for b in breaks if b.end_at is None), None)
        rows.append(
            {
                "shift": shift,
                "employee": shift.employee,
                "on_break": current is not None,
                "break_started_at": current.start_at if current else None,
                "provisional": time_services.provisional_seconds(shift, at, breaks),
                "started_before_today": shift.clock_in_at.astimezone(restaurant.tz).date() < business_today(restaurant),
            }
        )
    return rows


def hours_for_day(restaurant, day, at):
    """Shifts whose local clock-in date is `day` (same rule as the timesheets).

    Closed shifts give exact worked time; open ones are shown separately as provisional.
    """
    qs = time_services.shifts_in_range(
        Shift.objects.filter(restaurant=restaurant).prefetch_related("breaks"), day, day, restaurant.tz
    )
    closed = provisional = 0
    closed_count = open_count = 0
    people = set()
    for shift in qs:
        breaks = list(shift.breaks.all())
        people.add(shift.employee_id)
        if shift.is_open:
            open_count += 1
            provisional += time_services.provisional_seconds(shift, at, breaks)
        else:
            closed_count += 1
            closed += time_services.worked_seconds(shift, breaks)
    return {
        "closed_seconds": closed,
        "provisional_seconds": provisional,
        "closed_shifts": closed_count,
        "open_shifts": open_count,
        "people": len(people),
    }


def checklist_status(restaurant, day):
    """US-07.02 — progress per run and every unanswered task for the day."""
    runs = list(
        ChecklistRun.objects.filter(restaurant=restaurant, business_date=day)
        .select_related("started_by", "submitted_by")
        .order_by("kind", "title", "run_label")
    )
    summary = {"runs": [], "submitted": 0, "in_progress": 0, "answered": 0, "total": 0, "pending_required": 0}
    for run in runs:
        counts = run_counts(run)
        summary["runs"].append({"run": run, "counts": counts})
        summary["submitted" if run.is_submitted else "in_progress"] += 1
        summary["answered"] += counts["answered"]
        summary["total"] += counts["total"]
        summary["pending_required"] += counts["pending_required"]
    started = {r.template_id for r in runs}
    not_started = list(
        ChecklistTemplate.objects.filter(restaurant=restaurant, is_active=True).exclude(pk__in=started).order_by("kind", "title")
    )
    summary["expected"] = len(runs) + len(not_started)
    summary["percent"] = round(100 * summary["answered"] / summary["total"]) if summary["total"] else None

    # There are no task assignments in this release, so the responsible person is the
    # one who started the run; the page says so.
    incomplete = list(
        ChecklistResponse.objects.filter(run__in=runs, status=ResponseStatus.PENDING)
        .select_related("task", "run", "run__started_by")
        .order_by("-task__required", "run__kind", "run__title", "task__position")
    )
    return summary, incomplete, not_started


def issues(restaurant, day):
    """US-07.04 — every open issue (whatever its date) plus issues resolved on `day`."""
    base = ChecklistIssue.objects.filter(restaurant=restaurant).select_related(
        "run", "response__task", "reported_by", "resolved_by"
    )
    open_issues = list(base.filter(status=ChecklistIssue.Status.OPEN).order_by("reported_at", "id"))
    start, end = day_range_utc(day, day, restaurant.tz)
    resolved = list(
        base.filter(status=ChecklistIssue.Status.RESOLVED, resolved_at__gte=start, resolved_at__lt=end).order_by(
            "-resolved_at"
        )
    )
    return open_issues, resolved


def low_stock(restaurant):
    """US-07.03 — active items at or below their minimum, most urgent first."""
    items = list(
        InventoryItem.objects.filter(
            restaurant=restaurant, is_active=True, current_quantity__lte=F("minimum_quantity")
        ).order_by("name")
    )
    # Most urgent first: an empty item before one still above zero; then by how far below minimum.
    items.sort(key=lambda i: (i.current_quantity > 0, i.current_quantity - i.minimum_quantity, i.name.lower()))
    return items


def flagged_temperatures(restaurant, day):
    return list(
        ChecklistResponse.objects.filter(
            run__restaurant=restaurant,
            run__business_date=day,
            task__response_type=ResponseType.TEMPERATURE,
            temperature_out_of_range=True,
        )
        .select_related("task", "run", "answered_by")
        .prefetch_related("issues")
        .order_by("answered_at")
    )


def build(user, day=None):
    if not is_manager(user):
        raise PermissionDenied
    restaurant = user.restaurant
    at = now()
    today = business_today(restaurant)
    day = day or today
    summary, incomplete, not_started = checklist_status(restaurant, day)
    open_issues, resolved = issues(restaurant, day)
    return Dashboard(
        day=day,
        is_today=(day == today),
        generated_at=at,
        staff_on_shift=staff_status(restaurant, at),
        hours=hours_for_day(restaurant, day, at),
        checklists=summary,
        incomplete_tasks=incomplete,
        not_started=not_started,
        open_issues=open_issues,
        resolved_issues=resolved,
        low_stock=low_stock(restaurant),
        flagged_temperatures=flagged_temperatures(restaurant, day),
    )
