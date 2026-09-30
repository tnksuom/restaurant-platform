from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.permissions import is_manager, manager_required
from core.timeutils import business_today
from staff.models import Employee

from . import photos, services
from .forms import IssueForm, PhotoForm, ResolveForm, TaskFormSet, TemplateForm
from .models import (
    ChecklistIssue,
    ChecklistResponse,
    ChecklistRun,
    ChecklistTemplate,
    IssueAttachment,
    ResponseStatus,
    ResponseType,
    TemplateKind,
)


def _error_text(exc):
    return " ".join(exc.messages)


ANSWER_CHOICES = {
    ResponseType.PASS_FAIL: lambda: [("PASS", _("Pass")), ("FAIL", _("Fail")), ("NOT_APPLICABLE", _("N/A"))],
    ResponseType.CONFIRMATION: lambda: [("PASS", _("Done")), ("FAIL", _("Not done")), ("NOT_APPLICABLE", _("N/A"))],
    ResponseType.TEMPERATURE: lambda: [("PASS", _("Record reading")), ("NOT_APPLICABLE", _("N/A"))],
    ResponseType.STOCK_LEVEL: lambda: [("PASS", _("Enough")), ("FAIL", _("Low")), ("NOT_APPLICABLE", _("N/A"))],
}


def answer_label(response_type, status):
    if status == ResponseStatus.PENDING:
        return _("Not answered")
    if response_type == ResponseType.TEMPERATURE and status == ResponseStatus.PASS:
        return _("Reading recorded")
    return dict(ANSWER_CHOICES[response_type]()).get(status, status)


# ------------------------------------------------------------------ staff: today & runs


def today(request):
    restaurant = request.user.restaurant
    day = business_today(restaurant)
    runs = list(
        ChecklistRun.objects.filter(restaurant=restaurant, business_date=day).select_related("template")
    )
    by_template = {}
    for run in runs:
        by_template.setdefault(run.template_id, []).append(run)
    cards = []
    for template in ChecklistTemplate.objects.filter(restaurant=restaurant, is_active=True):
        template_runs = by_template.pop(template.pk, [])
        if not template_runs:
            cards.append({"template": template, "run": None})
        for run in sorted(template_runs, key=lambda r: r.run_label):
            cards.append({"template": template, "run": run, "counts": services.run_counts(run)})
    # runs of templates deactivated after being started today
    for template_runs in by_template.values():
        for run in template_runs:
            cards.append({"template": run.template, "run": run, "counts": services.run_counts(run)})
    templates = ChecklistTemplate.objects.filter(restaurant=restaurant, is_active=True) if is_manager(request.user) else []
    return render(request, "checklists/today.html", {"cards": cards, "day": day, "templates": templates})


@require_POST
def start_run(request):
    try:
        run, created = services.start_run(
            request.user, request.POST.get("template"), request.POST.get("run_label", "")
        )
    except ValidationError as exc:
        messages.error(request, _error_text(exc))
        return redirect("checklists:today")
    if not created:
        messages.info(request, _("This checklist was already started today. Continue below."))
    return redirect("checklists:run", pk=run.pk)


def _get_run(request, pk):
    run = get_object_or_404(ChecklistRun, pk=pk, restaurant=request.user.restaurant)
    if not services.can_view_run(request.user, run):
        raise Http404
    return run


def run_detail(request, pk):
    run = _get_run(request, pk)
    tasks = []
    issues = {}
    for issue in run.issues.select_related("reported_by").prefetch_related("attachments"):
        issues.setdefault(issue.response_id, []).append(issue)
    feedback = request.session.pop(f"checklist-feedback-{run.pk}", None)
    groups = []
    for number, task in enumerate(run.tasks.select_related("response", "response__answered_by"), start=1):
        if not groups or groups[-1]["name"] != task.section:
            groups.append({"name": task.section, "tasks": [], "answered": 0})
        group = groups[-1]
        response = task.response
        group["tasks"].append({
            "number": number, "task": task, "response": response, "issues": issues.get(response.pk, []),
            "choices": ANSWER_CHOICES[task.response_type](),
            "answer_label": answer_label(task.response_type, response.status),
            "feedback": feedback if feedback and feedback["id"] == response.pk else None,
        })
        if response.status != ResponseStatus.PENDING:
            group["answered"] += 1
    return render(
        request,
        "checklists/run.html",
        {"run": run, "groups": groups, "counts": services.run_counts(run), "editable": not run.is_submitted,
         "has_groups": any(g["name"] for g in groups),
         "finish_feedback": feedback if feedback and feedback["id"] == "finish" else None},
    )


@require_POST
def save_response(request, pk, response_id):
    """One tap saves the answer and moves on to the next task.

    Feedback is shown inside the task's own card (the page scrolls there), not at the top.
    """
    run = _get_run(request, pk)
    note = request.POST.get("note", "")
    key = f"checklist-feedback-{run.pk}"
    try:
        services.save_response(
            request.user, response_id, request.POST.get("status", ""),
            temperature=request.POST.get("temperature"), note=note,
        )
    except ValidationError as exc:
        request.session[key] = {"id": int(response_id), "level": "error", "text": _error_text(exc), "note": note}
        return redirect(reverse("checklists:run", args=[run.pk]) + f"#task-{response_id}")
    response = ChecklistResponse.objects.select_related("task").get(pk=response_id)
    saved = _("Saved “%(task)s”: %(answer)s.") % {
        "task": response.task.text, "answer": answer_label(response.task.response_type, response.status)}
    if response.temperature_out_of_range:
        feedback = ("warning", saved + " " + _("The reading is outside the configured range, so an issue was opened for a manager."))
    elif response.status == ResponseStatus.FAIL:
        feedback = ("warning", saved + " " + _("A manager can see it in the issue list."))
    else:
        feedback = ("success", saved)
    ids = list(run.tasks.order_by("position", "id").values_list("response__pk", flat=True))
    position = ids.index(response.pk)
    if position + 1 < len(ids):
        # Show the confirmation on the next task, which is where the page lands.
        target, anchor = ids[position + 1], f"#task-{ids[position + 1]}"
    else:
        target, anchor = "finish", "#finish"
    request.session[key] = {"id": target, "level": feedback[0], "text": str(feedback[1])}
    return redirect(reverse("checklists:run", args=[run.pk]) + anchor)


def submit_run(request, pk):
    run = _get_run(request, pk)
    if run.is_submitted:
        return redirect("checklists:run", pk=run.pk)
    if request.method == "POST":
        try:
            services.submit_run(request.user, run.pk)
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        else:
            messages.success(request, _("Checklist submitted. Thank you."))
            return redirect("checklists:run", pk=run.pk)
    return render(
        request,
        "checklists/submit.html",
        {"run": run, "missing": services.missing_required(run), "counts": services.run_counts(run),
         "open_issues": run.issues.filter(status=ChecklistIssue.Status.OPEN).count()},
    )


def report_issue(request, pk, response_id):
    run = _get_run(request, pk)
    response = get_object_or_404(ChecklistResponse, pk=response_id, run=run)
    form = IssueForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            issue = services.report_issue(
                request.user, response.pk, form.cleaned_data["description"], request.FILES.get("photo")
            )
        except ValidationError as exc:
            for field, errs in (exc.message_dict.items() if hasattr(exc, "error_dict") else [(None, exc.messages)]):
                form.add_error(field if field in form.fields else None, errs)
        else:
            if issue.attachments.exists():
                messages.success(request, _("Issue reported with photo."))
            else:
                messages.success(request, _("Issue reported."))
            return redirect(reverse("checklists:run", args=[run.pk]) + f"#task-{response.pk}")
    return render(request, "checklists/report_issue.html", {"run": run, "response": response, "form": form})


# ------------------------------------------------------------------ issues & photos


def issue_detail(request, pk):
    issue = get_object_or_404(
        ChecklistIssue.objects.select_related("run", "response__task", "reported_by", "resolved_by"),
        pk=pk, restaurant=request.user.restaurant,
    )
    if not services.can_view_issue(request.user, issue):
        raise Http404
    photo_form = PhotoForm(request.POST or None, request.FILES or None) if request.POST.get("action") == "photo" else PhotoForm()
    resolve_form = ResolveForm(request.POST or None) if request.POST.get("action") == "resolve" else ResolveForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "photo" and photo_form.is_valid():
            try:
                services.add_photo(request.user, issue.pk, request.FILES["photo"])
            except ValidationError as exc:
                photo_form.add_error("photo", _error_text(exc))
            except PermissionDenied:
                raise
            else:
                messages.success(request, _("Photo uploaded."))
                return redirect("checklists:issue", pk=issue.pk)
        elif action == "resolve" and resolve_form.is_valid():
            try:
                services.resolve_issue(request.user, issue.pk, resolve_form.cleaned_data["resolution_note"])
            except ValidationError as exc:
                resolve_form.add_error(None, _error_text(exc))
            else:
                messages.success(request, _("Issue marked resolved."))
                return redirect("checklists:issue", pk=issue.pk)
    return render(
        request,
        "checklists/issue.html",
        {
            "issue": issue, "photo_form": photo_form, "resolve_form": resolve_form,
            "can_add_photo": services.can_add_photo(request.user, issue),
            "can_resolve": is_manager(request.user) and issue.status == ChecklistIssue.Status.OPEN,
        },
    )


def photo(request, pk):
    attachment = get_object_or_404(IssueAttachment.objects.select_related("issue"), pk=pk)
    if not services.can_view_issue(request.user, attachment.issue):
        raise Http404
    try:
        fh = open(photos.path_for(attachment.storage_key), "rb")
    except FileNotFoundError:
        raise Http404
    response = FileResponse(fh, content_type=attachment.content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    response["Cache-Control"] = "private, no-store"
    return response


@manager_required
def issue_list(request):
    status = request.GET.get("status", "OPEN")
    qs = ChecklistIssue.objects.filter(restaurant=request.user.restaurant).select_related(
        "run", "response__task", "reported_by"
    )
    if status in ChecklistIssue.Status.values:
        qs = qs.filter(status=status)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "checklists/issues.html", {"page": page, "status": status})


# ------------------------------------------------------------------ manager review & history


@manager_required
def review(request):
    restaurant = request.user.restaurant
    day = parse_date(request.GET.get("date", "") or "") or business_today(restaurant)
    runs = list(ChecklistRun.objects.filter(restaurant=restaurant, business_date=day).select_related(
        "started_by", "submitted_by"))
    started_templates = {r.template_id for r in runs}
    not_started = ChecklistTemplate.objects.filter(restaurant=restaurant, is_active=True).exclude(
        pk__in=started_templates
    )
    rows = []
    totals = {"runs": len(runs), "submitted": 0, "in_progress": 0, "not_started": not_started.count(),
              "failed": 0, "pending_required": 0, "warnings": 0}
    for run in runs:
        counts = services.run_counts(run)
        rows.append({"run": run, "counts": counts})
        totals["submitted" if run.is_submitted else "in_progress"] += 1
        totals["failed"] += counts["failed"]
        totals["pending_required"] += counts["pending_required"]
        totals["warnings"] += counts["warnings"]
    readings = ChecklistResponse.objects.filter(
        run__in=runs, task__response_type=ResponseType.TEMPERATURE
    ).exclude(status=ResponseStatus.PENDING).select_related("task", "run", "answered_by").order_by("answered_at")
    open_issues = ChecklistIssue.objects.filter(run__in=runs, status=ChecklistIssue.Status.OPEN).select_related(
        "response__task", "run", "reported_by")
    expected = totals["runs"] + totals["not_started"]
    return render(
        request,
        "checklists/review.html",
        {"day": day, "rows": rows, "not_started": not_started, "totals": totals, "expected": expected,
         "readings": readings, "open_issues": open_issues},
    )


def history(request):
    """Managers see all runs; employees see submitted runs they took part in."""
    restaurant = request.user.restaurant
    manager = is_manager(request.user)
    qs = ChecklistRun.objects.filter(restaurant=restaurant).select_related("started_by", "submitted_by")
    if not manager:
        me = request.user
        qs = qs.filter(status=ChecklistRun.Status.SUBMITTED).filter(
            Q(submitted_by=me) | Q(started_by=me) | Q(responses__answered_by=me)
        ).distinct()
    f = {k: request.GET.get(k, "") for k in ("from", "to", "template", "status", "employee")}
    date_from, date_to = parse_date(f["from"] or ""), parse_date(f["to"] or "")
    if date_from:
        qs = qs.filter(business_date__gte=date_from)
    if date_to:
        qs = qs.filter(business_date__lte=date_to)
    if f["template"].isdigit():
        qs = qs.filter(template_id=int(f["template"]))
    if f["status"] in ChecklistRun.Status.values:
        qs = qs.filter(status=f["status"])
    if manager and f["employee"].isdigit():
        emp = int(f["employee"])
        qs = qs.filter(Q(submitted_by_id=emp) | Q(started_by_id=emp) | Q(responses__answered_by_id=emp)).distinct()
    page = Paginator(qs.order_by("-business_date", "title", "run_label"), 50).get_page(request.GET.get("page"))
    for run in page:
        run.counts = services.run_counts(run)
    return render(
        request,
        "checklists/history.html",
        {
            "page": page, "f": f, "statuses": ChecklistRun.Status.choices,
            "templates": ChecklistTemplate.objects.filter(restaurant=restaurant),
            "staff": Employee.objects.filter(restaurant=restaurant) if manager else [],
        },
    )


# ------------------------------------------------------------------ templates


@manager_required
def template_list(request):
    templates = ChecklistTemplate.objects.filter(restaurant=request.user.restaurant).order_by("-is_active", "kind", "title")
    return render(request, "checklists/templates.html", {"templates": templates})


@manager_required
def template_edit(request, pk=None):
    template = None
    if pk:
        template = get_object_or_404(ChecklistTemplate, pk=pk, restaurant=request.user.restaurant)
        initial_tasks = [
            {
                "id": t.pk, "position": t.position, "section": t.section, "text": t.text,
                "response_type": t.response_type,
                "required": t.required, "equipment_or_location": t.equipment_or_location,
                "min_temperature": t.min_temperature, "max_temperature": t.max_temperature,
            }
            for t in template.active_tasks()
        ]
        form = TemplateForm(request.POST or None, initial={"title": template.title, "kind": template.kind})
    else:
        initial_tasks = []
        form = TemplateForm(request.POST or None, initial={"kind": TemplateKind.OPENING})
    formset = TaskFormSet(request.POST or None, initial=initial_tasks, prefix="tasks")
    error = None
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        tasks = [f.cleaned_data for f in formset.forms if f.cleaned_data]
        pasted = services.parse_task_lines(form.cleaned_data.pop("paste_tasks", ""))
        paste_type = form.cleaned_data.pop("paste_response_type")
        start = max([t.get("position") or 0 for t in tasks] + [len(tasks)])
        for offset, (group, text) in enumerate(pasted, start=1):
            tasks.append({"position": start + offset, "section": group, "text": text,
                          "response_type": paste_type, "required": True})
        try:
            template = services.save_template(request.user, template, tasks=tasks, **form.cleaned_data)
        except ValidationError as exc:
            error = _error_text(exc)
        else:
            if pasted:
                messages.success(request, _("%(n)s task(s) added from the pasted list.") % {"n": len(pasted)})
            messages.success(request, _("Checklist saved (version %(v)s). Runs already started keep their original tasks.") % {"v": template.version})
            return redirect("checklists:templates")
    return render(
        request, "checklists/template_form.html",
        {"form": form, "formset": formset, "template": template, "error": error},
    )


@manager_required
@require_POST
def template_set_active(request, pk):
    template = get_object_or_404(ChecklistTemplate, pk=pk, restaurant=request.user.restaurant)
    active = request.POST.get("active") == "1"
    services.set_template_active(request.user, template, active)
    messages.success(request, _("Checklist activated.") if active else _("Checklist deactivated. Its history is kept."))
    return redirect("checklists:templates")
