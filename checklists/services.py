"""Checklist rules (EP05)."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from core import audit
from core.permissions import is_manager
from core.timeutils import business_today, now

from . import photos
from .models import (
    ChecklistIssue,
    ChecklistResponse,
    ChecklistRun,
    ChecklistTemplate,
    IssueAttachment,
    ResponseStatus,
    ResponseType,
    RunTask,
    TemplateTask,
)


def _require_manager(actor):
    if not is_manager(actor):
        raise PermissionDenied


def _require_active(actor):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied


# ---------------------------------------------------------------- templates


def _task_snapshot(t):
    return {
        "position": t.position, "text": t.text, "response_type": t.response_type, "required": t.required,
        "equipment_or_location": t.equipment_or_location,
        "min_temperature": None if t.min_temperature is None else str(t.min_temperature),
        "max_temperature": None if t.max_temperature is None else str(t.max_temperature),
    }


@transaction.atomic
def save_template(actor, template, *, title, kind, tasks):
    """Create or update a template and its tasks.

    `tasks` is a list of dicts: id (optional), position, text, response_type, required,
    equipment_or_location, min_temperature, max_temperature, remove (bool).
    Removed tasks are deactivated, never deleted, because runs may reference them.
    Every save bumps the version; runs keep their own snapshot anyway.
    """
    _require_manager(actor)
    title = (title or "").strip()
    if not title:
        raise ValidationError({"title": _("Enter a title.")})
    creating = template is None
    if creating:
        template = ChecklistTemplate(restaurant=actor.restaurant, created_by=actor, title=title, kind=kind)
    else:
        template = ChecklistTemplate.objects.select_for_update().get(pk=template.pk, restaurant=actor.restaurant)
        template.title, template.kind = title, kind
        template.version += 1
    template.full_clean(exclude=["restaurant", "created_by"])
    template.save()

    existing = {t.pk: t for t in template.tasks.all()} if not creating else {}
    active_count = 0
    errors = []
    for i, data in enumerate(tasks, start=1):
        task = existing.get(data.get("id")) if data.get("id") else None
        if data.get("id") and task is None:
            raise ValidationError(_("A task does not belong to this checklist. Reload and try again."))
        if data.get("remove"):
            if task and task.is_active:
                task.is_active = False
                task.save(update_fields=["is_active"])
            continue
        text = (data.get("text") or "").strip()
        if not text:
            if task is None:
                continue  # empty extra row
            errors.append(_("Task %(n)s needs a description.") % {"n": i})
            continue
        task = task or TemplateTask(template=template)
        task.position = data.get("position") or i
        task.text = text
        task.response_type = data["response_type"]
        task.required = bool(data.get("required"))
        task.equipment_or_location = (data.get("equipment_or_location") or "").strip()
        lo, hi = data.get("min_temperature"), data.get("max_temperature")
        if task.response_type != ResponseType.TEMPERATURE:
            lo = hi = None
        elif lo is not None and hi is not None and lo > hi:
            errors.append(_("Task %(n)s: the lowest temperature must not be above the highest.") % {"n": i})
        task.min_temperature, task.max_temperature = lo, hi
        task.is_active = True
        task.save()
        active_count += 1
    if errors:
        raise ValidationError(errors)
    if active_count == 0:
        raise ValidationError(_("A checklist needs at least one task."))
    audit.record(
        actor,
        "checklist.template_created" if creating else "checklist.template_updated",
        template,
        {"version": template.version, "title": title, "tasks": [_task_snapshot(t) for t in template.active_tasks()]},
    )
    return template


@transaction.atomic
def set_template_active(actor, template, active):
    _require_manager(actor)
    template = ChecklistTemplate.objects.select_for_update().get(pk=template.pk, restaurant=actor.restaurant)
    if template.is_active != active:
        template.is_active = active
        template.save(update_fields=["is_active", "updated_at"])
        audit.record(actor, "checklist.template_activated" if active else "checklist.template_deactivated", template)
    return template


# ---------------------------------------------------------------- runs


def start_run(actor, template_id, run_label=""):
    """Start (or resume) today's run for a template. Returns (run, created)."""
    _require_active(actor)
    run_label = (run_label or "").strip()
    if run_label and not is_manager(actor):
        raise PermissionDenied
    template = ChecklistTemplate.objects.filter(pk=template_id, restaurant=actor.restaurant).first()
    if template is None:
        raise ValidationError(_("Checklist not found."))
    day = business_today(actor.restaurant)
    existing = ChecklistRun.objects.filter(template=template, business_date=day, run_label=run_label).first()
    if existing:
        if run_label:
            raise ValidationError(_("A run with this label already exists today."))
        return existing, False
    if not template.is_active:
        raise ValidationError(_("This checklist is deactivated."))
    tasks = list(template.active_tasks())
    if not tasks:
        raise ValidationError(_("This checklist has no tasks."))
    try:
        with transaction.atomic():
            run = ChecklistRun.objects.create(
                restaurant=actor.restaurant, template=template, template_version=template.version,
                title=template.title, kind=template.kind, business_date=day, run_label=run_label,
                started_by=actor, started_at=now(),
            )
            for t in tasks:
                snap = RunTask.objects.create(
                    run=run, source_task=t, position=t.position, text=t.text, response_type=t.response_type,
                    required=t.required, equipment_or_location=t.equipment_or_location,
                    min_temperature=t.min_temperature, max_temperature=t.max_temperature,
                )
                ChecklistResponse.objects.create(run=run, task=snap)
    except IntegrityError:
        # Someone else started the same run at the same moment: use theirs.
        return ChecklistRun.objects.get(template=template, business_date=day, run_label=run_label), False
    return run, True


def _lock_open_run(run_id, actor):
    run = ChecklistRun.objects.select_for_update().filter(pk=run_id, restaurant=actor.restaurant).first()
    if run is None:
        raise ValidationError(_("Checklist not found."))
    if run.is_submitted:
        raise ValidationError(_("This checklist was already submitted and can no longer be changed."))
    return run


def parse_temperature(value):
    text = str(value if value is not None else "").strip().replace(",", ".").replace("−", "-")
    if not text:
        return None
    try:
        t = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValidationError({"temperature": _("Enter the reading as a number, for example 3.5 or -18")})
    if not t.is_finite() or t < Decimal("-100") or t > Decimal("400"):
        raise ValidationError({"temperature": _("That reading looks wrong. Enter degrees Celsius.")})
    if t.as_tuple().exponent < -2:
        raise ValidationError({"temperature": _("Use at most two decimal places.")})
    return t


def out_of_range(task, reading):
    """None when no thresholds are configured: the app does not guess what is acceptable."""
    if reading is None or not task.has_thresholds:
        return None
    too_low = task.min_temperature is not None and reading < task.min_temperature
    too_high = task.max_temperature is not None and reading > task.max_temperature
    return too_low or too_high


def _num(value):
    return format(value.normalize(), "f")


def _range_text(task):
    lo = f"{_num(task.min_temperature)} °C" if task.min_temperature is not None else "—"
    hi = f"{_num(task.max_temperature)} °C" if task.max_temperature is not None else "—"
    return f"{lo} … {hi}"


@transaction.atomic
def save_response(actor, response_id, status, *, temperature=None, note=""):
    _require_active(actor)
    response = ChecklistResponse.objects.select_related("task").filter(
        pk=response_id, run__restaurant=actor.restaurant
    ).first()
    if response is None:
        raise ValidationError(_("Task not found."))
    run = _lock_open_run(response.run_id, actor)
    response = ChecklistResponse.objects.select_for_update().select_related("task").get(pk=response.pk)
    task = response.task
    note = (note or "").strip()
    allowed = {
        ResponseType.PASS_FAIL: {ResponseStatus.PASS, ResponseStatus.FAIL, ResponseStatus.NOT_APPLICABLE},
        ResponseType.CONFIRMATION: {ResponseStatus.PASS, ResponseStatus.FAIL, ResponseStatus.NOT_APPLICABLE},
        ResponseType.TEMPERATURE: {ResponseStatus.PASS, ResponseStatus.NOT_APPLICABLE},
    }[task.response_type]
    if status not in allowed:
        raise ValidationError({"status": _("Choose an answer.")})
    if status in (ResponseStatus.FAIL, ResponseStatus.NOT_APPLICABLE) and not note:
        raise ValidationError({"note": _("Add a note explaining this answer.")})
    reading = None
    if task.response_type == ResponseType.TEMPERATURE and status == ResponseStatus.PASS:
        reading = parse_temperature(temperature)
        if reading is None:
            raise ValidationError({"temperature": _("Enter the temperature reading.")})

    response.status = status
    response.temperature_c = reading
    response.temperature_out_of_range = out_of_range(task, reading)
    response.note = note
    response.answered_by = actor
    response.answered_at = now()
    response.save()

    # Exceptions become (or link to) an open issue. Issues never auto-resolve.
    if status == ResponseStatus.FAIL:
        _ensure_open_issue(actor, run, response, ChecklistIssue.Source.FAILED_TASK, note)
    if response.temperature_out_of_range:
        where = f" ({task.equipment_or_location})" if task.equipment_or_location else ""
        desc = _("Reading %(reading)s °C%(where)s is outside the configured range %(range)s.") % {
            "reading": _num(reading), "where": where, "range": _range_text(task),
        }
        if note:
            desc += " " + note
        _ensure_open_issue(actor, run, response, ChecklistIssue.Source.TEMPERATURE, desc)
    return response


def _ensure_open_issue(actor, run, response, source, description):
    issue = ChecklistIssue.objects.filter(response=response, source=source, status=ChecklistIssue.Status.OPEN).first()
    if issue:
        return issue, False
    issue = ChecklistIssue.objects.create(
        restaurant=run.restaurant, run=run, response=response, source=source,
        description=description, reported_by=actor, reported_at=now(),
    )
    return issue, True


def report_issue(actor, response_id, description, photo=None):
    """Explicitly flag an issue on a task. If a photo is included it must be valid,
    otherwise nothing is saved (no issue that promises a missing photo)."""
    _require_active(actor)
    description = (description or "").strip()
    if not description:
        raise ValidationError({"description": _("Describe the problem.")})
    prepared = photos.prepare_image(photo) if photo else None
    key = photos.write_private(prepared[0], prepared[2]) if prepared else None
    try:
        with transaction.atomic():
            response = ChecklistResponse.objects.filter(pk=response_id, run__restaurant=actor.restaurant).first()
            if response is None:
                raise ValidationError(_("Task not found."))
            run = ChecklistRun.objects.select_for_update().get(pk=response.run_id)
            if run.is_submitted and not is_manager(actor):
                raise ValidationError(_("This checklist was already submitted. Ask a manager to follow up."))
            issue = ChecklistIssue.objects.create(
                restaurant=run.restaurant, run=run, response=response, source=ChecklistIssue.Source.REPORTED,
                description=description, reported_by=actor, reported_at=now(),
            )
            if prepared:
                _attach(actor, issue, key, prepared, photo.name)
    except Exception:
        if key:
            photos.delete_private(key)
        raise
    return issue


def _attach(actor, issue, key, prepared, filename):
    data, content_type, _ext = prepared
    return IssueAttachment.objects.create(
        issue=issue, storage_key=key, original_filename=(filename or "photo")[:255],
        content_type=content_type, byte_size=len(data), uploaded_by=actor, uploaded_at=now(),
    )


def can_view_issue(user, issue):
    if not user.is_active or issue.restaurant_id != user.restaurant_id:
        return False
    return is_manager(user) or issue.reported_by_id == user.pk or can_view_run(user, issue.run)


def can_add_photo(user, issue):
    if not user.is_active or issue.restaurant_id != user.restaurant_id:
        return False
    if is_manager(user):
        return True
    return issue.status == ChecklistIssue.Status.OPEN and issue.reported_by_id == user.pk


def add_photo(actor, issue_id, photo):
    _require_active(actor)
    issue = ChecklistIssue.objects.filter(pk=issue_id, restaurant=actor.restaurant).first()
    if issue is None or not can_add_photo(actor, issue):
        raise PermissionDenied
    prepared = photos.prepare_image(photo)
    key = photos.write_private(prepared[0], prepared[2])
    try:
        with transaction.atomic():
            return _attach(actor, issue, key, prepared, photo.name)
    except Exception:
        photos.delete_private(key)
        raise


@transaction.atomic
def resolve_issue(actor, issue_id, resolution_note):
    _require_manager(actor)
    note = (resolution_note or "").strip()
    if not note:
        raise ValidationError({"resolution_note": _("Describe how the issue was resolved.")})
    issue = ChecklistIssue.objects.select_for_update().filter(pk=issue_id, restaurant=actor.restaurant).first()
    if issue is None:
        raise ValidationError(_("Issue not found."))
    if issue.status == ChecklistIssue.Status.RESOLVED:
        raise ValidationError(_("This issue is already resolved."))
    issue.status = ChecklistIssue.Status.RESOLVED
    issue.resolved_by = actor
    issue.resolved_at = now()
    issue.resolution_note = note
    issue.save()
    audit.record(actor, "checklist.issue_resolved", issue, {"resolution_note": note})
    return issue


def missing_required(run):
    return list(
        run.tasks.filter(required=True, response__status=ResponseStatus.PENDING).order_by("position", "id")
    )


@transaction.atomic
def submit_run(actor, run_id):
    _require_active(actor)
    run = _lock_open_run(run_id, actor)
    missing = missing_required(run)
    if missing:
        raise ValidationError(
            _("%(n)s required task(s) still need an answer.") % {"n": len(missing)}
        )
    run.status = ChecklistRun.Status.SUBMITTED
    run.submitted_by = actor
    run.submitted_at = now()
    run.save()
    audit.record(actor, "checklist.run_submitted", run)
    return run


def can_view_run(user, run):
    """Managers: everything. Employees: today's in-progress runs (so they can fill them)
    and submitted runs they took part in."""
    if not user.is_active or run.restaurant_id != user.restaurant_id:
        return False
    if is_manager(user):
        return True
    if not run.is_submitted:
        return run.business_date == business_today(user.restaurant)
    return (
        run.submitted_by_id == user.pk
        or run.started_by_id == user.pk
        or run.responses.filter(answered_by=user).exists()
    )


def run_counts(run):
    counts = {"total": 0, "answered": 0, "pending": 0, "pending_required": 0, "failed": 0, "na": 0, "warnings": 0}
    for r in run.responses.select_related("task"):
        counts["total"] += 1
        if r.status == ResponseStatus.PENDING:
            counts["pending"] += 1
            if r.task.required:
                counts["pending_required"] += 1
        else:
            counts["answered"] += 1
        if r.status == ResponseStatus.FAIL:
            counts["failed"] += 1
        if r.status == ResponseStatus.NOT_APPLICABLE:
            counts["na"] += 1
        if r.temperature_out_of_range:
            counts["warnings"] += 1
    counts["percent"] = round(100 * counts["answered"] / counts["total"]) if counts["total"] else None
    return counts
