"""Time recording rules (EP06). Server time is authoritative for every clock action."""
from datetime import datetime
from datetime import timezone as dt_timezone

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from core.permissions import is_manager
from core.timeutils import day_range_utc, now
from staff.models import Employee

from .models import Break, Shift, ShiftEvent, TimeCorrection


class ClockError(ValidationError):
    pass


def _lock_employee(employee):
    """Serialize all clock actions for one person."""
    emp = Employee.objects.select_for_update().get(pk=employee.pk)
    if not emp.is_active:
        raise PermissionDenied
    return emp


def open_shift(employee):
    return Shift.objects.filter(employee=employee, status=Shift.Status.OPEN).first()


def _event(shift, type_, at, actor):
    ShiftEvent.objects.create(shift=shift, type=type_, at=at, actor=actor)


@transaction.atomic
def clock_in(employee):
    emp = _lock_employee(employee)
    if open_shift(emp):
        raise ClockError(_("You are already clocked in."))
    ts = now()
    try:
        with transaction.atomic():
            shift = Shift.objects.create(restaurant=emp.restaurant, employee=emp, clock_in_at=ts)
    except IntegrityError:
        raise ClockError(_("You are already clocked in."))
    _event(shift, ShiftEvent.Type.CLOCK_IN, ts, emp)
    return shift


def _locked_open_shift(emp):
    shift = Shift.objects.select_for_update().filter(employee=emp, status=Shift.Status.OPEN).first()
    if shift is None:
        raise ClockError(_("You are not clocked in."))
    return shift


@transaction.atomic
def start_break(employee):
    emp = _lock_employee(employee)
    shift = _locked_open_shift(emp)
    current = shift.breaks.filter(end_at__isnull=True).first()
    if current:
        return current, False  # idempotent: the break is already running
    ts = now()
    brk = Break.objects.create(shift=shift, start_at=ts)
    _event(shift, ShiftEvent.Type.BREAK_START, ts, emp)
    return brk, True


@transaction.atomic
def end_break(employee):
    emp = _lock_employee(employee)
    shift = _locked_open_shift(emp)
    current = shift.breaks.select_for_update().filter(end_at__isnull=True).first()
    if current is None:
        raise ClockError(_("You are not on a break."))
    ts = now()
    if ts <= current.start_at:
        raise ClockError(_("The break has not lasted any time yet. Try again in a moment."))
    current.end_at = ts
    current.save(update_fields=["end_at"])
    _event(shift, ShiftEvent.Type.BREAK_END, ts, emp)
    return current


@transaction.atomic
def clock_out(employee):
    emp = _lock_employee(employee)
    shift = _locked_open_shift(emp)
    if shift.breaks.filter(end_at__isnull=True).exists():
        raise ClockError(_("You are on a break. End your break first, then clock out."))
    ts = now()
    if ts <= shift.clock_in_at:
        raise ClockError(_("You clocked in just now. Try again in a moment."))
    shift.clock_out_at = ts
    shift.status = Shift.Status.CLOSED
    shift.save(update_fields=["clock_out_at", "status", "updated_at"])
    _event(shift, ShiftEvent.Type.CLOCK_OUT, ts, emp)
    return shift


# ------------------------------------------------------------------ durations


def break_seconds(shift, breaks=None):
    breaks = shift.breaks.all() if breaks is None else breaks
    return sum(b.seconds for b in breaks)


def worked_seconds(shift, breaks=None):
    """Exact elapsed seconds for a closed shift: instants, so DST is handled."""
    if shift.clock_out_at is None:
        return None
    return int((shift.clock_out_at - shift.clock_in_at).total_seconds()) - break_seconds(shift, breaks)


def provisional_seconds(shift, at=None, breaks=None):
    """Live duration of an open shift so far, excluding completed and running breaks. Not approved time."""
    at = at or now()
    breaks = list(shift.breaks.all()) if breaks is None else breaks
    total = int((at - shift.clock_in_at).total_seconds()) - break_seconds(shift, breaks)
    for b in breaks:
        if b.end_at is None:
            total -= int((at - b.start_at).total_seconds())
    return max(total, 0)


def shifts_in_range(qs, date_from, date_to, tz):
    """Shifts whose LOCAL clock-in date falls in [date_from, date_to]."""
    start, end = day_range_utc(date_from, date_to, tz)
    return qs.filter(clock_in_at__gte=start, clock_in_at__lt=end)


def summarize(shifts):
    rows, closed_total, open_count = [], 0, 0
    at = now()
    for shift in shifts:
        breaks = list(shift.breaks.all())
        worked = worked_seconds(shift, breaks)
        row = {"shift": shift, "breaks": breaks, "break_seconds": break_seconds(shift, breaks), "worked": worked}
        if shift.is_open:
            open_count += 1
            row["provisional"] = provisional_seconds(shift, at, breaks)
            row["on_break"] = any(b.end_at is None for b in breaks)
        else:
            closed_total += worked
        rows.append(row)
    return rows, closed_total, open_count


# ------------------------------------------------------------------ corrections


def _iso(dt):
    return dt.astimezone(dt_timezone.utc).isoformat() if dt else None


def snapshot(shift):
    """Before/after record. Always UTC so equal instants compare equal."""
    return {
        "clock_in_at": _iso(shift.clock_in_at),
        "clock_out_at": _iso(shift.clock_out_at),
        "status": shift.status,
        "breaks": [{"start_at": _iso(b.start_at), "end_at": _iso(b.end_at)} for b in shift.breaks.order_by("start_at", "id")],
    }


def validate_times(clock_in_at, clock_out_at, breaks, at):
    """Chronological order, break containment, non-overlap, nonnegative worked time."""
    errors = []
    if clock_in_at > at:
        errors.append(_("Clock-in cannot be in the future."))
    if clock_out_at is not None:
        if clock_out_at <= clock_in_at:
            errors.append(_("Clock-out must be after clock-in."))
        if clock_out_at > at:
            errors.append(_("Clock-out cannot be in the future."))
    end_limit = clock_out_at or at
    ordered = sorted(breaks, key=lambda b: b[0])
    open_breaks = [b for b in ordered if b[1] is None]
    if open_breaks and clock_out_at is not None:
        errors.append(_("Every break needs an end time when the shift has a clock-out."))
    if len(open_breaks) > 1:
        errors.append(_("Only one break can be still running."))
    for i, (start, end) in enumerate(ordered):
        if start < clock_in_at or start >= end_limit:
            errors.append(_("Each break must start during the shift."))
        if end is not None:
            if end <= start:
                errors.append(_("A break must end after it starts."))
            if end > end_limit:
                errors.append(_("Each break must end within the shift."))
        if i + 1 < len(ordered):
            next_start = ordered[i + 1][0]
            if end is None or end > next_start:
                errors.append(_("Breaks must not overlap."))
    if errors:
        raise ValidationError(sorted(set(errors), key=errors.index))


@transaction.atomic
def correct_shift(actor, shift, *, clock_in_at, clock_out_at, breaks, reason):
    """Replace a shift's times after validation. `breaks` is a list of (start, end|None).

    The previous values are kept in an append-only TimeCorrection row together with
    the new values, the manager and the time. Clock events are never edited.
    """
    if not is_manager(actor):
        raise PermissionDenied
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": _("Give a reason for the correction.")})
    shift = Shift.objects.select_for_update().get(pk=shift.pk, restaurant=actor.restaurant)
    Employee.objects.select_for_update().get(pk=shift.employee_id)  # serialize with clock actions
    at = now()
    if not shift.is_open and clock_out_at is None:
        raise ValidationError(_("A closed shift cannot be reopened. Enter a clock-out time."))
    validate_times(clock_in_at, clock_out_at, breaks, at)

    # No overlap with the same person's other shifts (an open shift runs until now).
    end = clock_out_at or at
    for other in Shift.objects.filter(employee_id=shift.employee_id).exclude(pk=shift.pk):
        other_end = other.clock_out_at or at
        if clock_in_at < other_end and other.clock_in_at < end:
            raise ValidationError(
                _("These times overlap another shift of this person (%(start)s). Correct that shift first.")
                % {"start": other.clock_in_at.astimezone(actor.restaurant.tz).strftime("%d.%m.%Y %H:%M")}
            )

    before = snapshot(shift)
    shift.clock_in_at = clock_in_at
    shift.clock_out_at = clock_out_at
    shift.status = Shift.Status.OPEN if clock_out_at is None else Shift.Status.CLOSED
    shift.breaks.all().delete()
    for start, end_at in sorted(breaks, key=lambda b: b[0]):
        Break.objects.create(shift=shift, start_at=start, end_at=end_at)
    after = snapshot(shift)
    if after == before:
        raise ValidationError(_("Nothing was changed."))
    shift.is_corrected = True
    shift.save()
    return TimeCorrection.objects.create(
        shift=shift, changed_by=actor, changed_at=at, reason=reason, before=before, after=after
    )


def parse_snapshot_times(snap, tz):
    """For display: ISO strings -> aware datetimes in tz."""

    def p(v):
        return datetime.fromisoformat(v).astimezone(tz) if v else None

    return {
        "clock_in_at": p(snap["clock_in_at"]),
        "clock_out_at": p(snap["clock_out_at"]),
        "breaks": [(p(b["start_at"]), p(b["end_at"])) for b in snap["breaks"]],
    }
