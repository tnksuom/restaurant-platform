from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import Restaurant

TEMP = {"max_digits": 6, "decimal_places": 2}
User = settings.AUTH_USER_MODEL


class TemplateKind(models.TextChoices):
    OPENING = "OPENING", _("Opening")
    CLOSING = "CLOSING", _("Closing")
    CLEANING = "CLEANING", _("Cleaning")
    FOOD_SAFETY = "FOOD_SAFETY", _("Food safety")
    STOCK_CHECK = "STOCK_CHECK", _("Stock check")
    OTHER = "OTHER", _("Other")


class ResponseType(models.TextChoices):
    PASS_FAIL = "PASS_FAIL", _("Pass / fail")
    TEMPERATURE = "TEMPERATURE", _("Temperature reading (°C)")
    CONFIRMATION = "CONFIRMATION", _("Done / not done")


class ChecklistTemplate(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="checklist_templates")
    title = models.CharField(_("title"), max_length=150)
    kind = models.CharField(_("type"), max_length=16, choices=TemplateKind.choices)
    is_active = models.BooleanField(_("active"), default=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "title", "id"]

    def __str__(self):
        return self.title

    def active_tasks(self):
        return self.tasks.filter(is_active=True).order_by("position", "id")


class TemplateTask(models.Model):
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, related_name="tasks")
    position = models.PositiveIntegerField(_("order"), default=0)
    text = models.CharField(_("task"), max_length=300)
    response_type = models.CharField(_("answer type"), max_length=16, choices=ResponseType.choices)
    required = models.BooleanField(_("required"), default=True)
    equipment_or_location = models.CharField(_("equipment or location"), max_length=150, blank=True)
    min_temperature = models.DecimalField(_("lowest acceptable °C"), **TEMP, null=True, blank=True)
    max_temperature = models.DecimalField(_("highest acceptable °C"), **TEMP, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(min_temperature__isnull=True)
                | Q(max_temperature__isnull=True)
                | Q(min_temperature__lte=models.F("max_temperature")),
                name="task_threshold_order",
            ),
        ]


class ChecklistRun(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        SUBMITTED = "SUBMITTED", _("Submitted")

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="checklist_runs")
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, related_name="runs")
    template_version = models.PositiveIntegerField()
    title = models.CharField(max_length=150)  # snapshot of template title
    kind = models.CharField(max_length=16, choices=TemplateKind.choices)  # snapshot
    business_date = models.DateField()
    run_label = models.CharField(_("run label"), max_length=60, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS)
    started_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    started_at = models.DateTimeField()
    submitted_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-business_date", "kind", "title", "run_label"]
        constraints = [
            # One run per template per day, unless a manager adds a distinctly labeled run.
            models.UniqueConstraint(fields=["template", "business_date", "run_label"], name="run_unique_per_day_label"),
            models.CheckConstraint(
                condition=Q(status="IN_PROGRESS", submitted_at__isnull=True, submitted_by__isnull=True)
                | Q(status="SUBMITTED", submitted_at__isnull=False, submitted_by__isnull=False),
                name="run_submission_consistent",
            ),
        ]

    def __str__(self):
        label = f" ({self.run_label})" if self.run_label else ""
        return f"{self.title}{label} {self.business_date}"

    @property
    def is_submitted(self):
        return self.status == self.Status.SUBMITTED


class RunTask(models.Model):
    """Snapshot of a template task at the moment the run started."""

    run = models.ForeignKey(ChecklistRun, on_delete=models.PROTECT, related_name="tasks")
    source_task = models.ForeignKey(TemplateTask, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    position = models.PositiveIntegerField()
    text = models.CharField(max_length=300)
    response_type = models.CharField(max_length=16, choices=ResponseType.choices)
    required = models.BooleanField()
    equipment_or_location = models.CharField(max_length=150, blank=True)
    min_temperature = models.DecimalField(**TEMP, null=True, blank=True)
    max_temperature = models.DecimalField(**TEMP, null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]

    @property
    def has_thresholds(self):
        return self.min_temperature is not None or self.max_temperature is not None


class ResponseStatus(models.TextChoices):
    PENDING = "PENDING", _("Not answered")
    PASS = "PASS", _("Pass")
    FAIL = "FAIL", _("Fail")
    NOT_APPLICABLE = "NOT_APPLICABLE", _("Not applicable")


class ChecklistResponse(models.Model):
    """Current answer for one snapshot task. Editable only while the run is in progress.

    For TEMPERATURE tasks, PASS means "reading recorded"; whether the reading is inside the
    manager's thresholds is stored separately in temperature_out_of_range (NULL when no
    thresholds are configured, so the system never implies compliance).
    """

    run = models.ForeignKey(ChecklistRun, on_delete=models.PROTECT, related_name="responses")
    task = models.OneToOneField(RunTask, on_delete=models.PROTECT, related_name="response")
    status = models.CharField(max_length=16, choices=ResponseStatus.choices, default=ResponseStatus.PENDING)
    temperature_c = models.DecimalField(**TEMP, null=True, blank=True)
    temperature_out_of_range = models.BooleanField(null=True, blank=True)
    note = models.TextField(blank=True)
    answered_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(status__in=["FAIL", "NOT_APPLICABLE"]) | ~Q(note=""),
                name="response_note_required",
            ),
            models.CheckConstraint(
                condition=Q(status="PENDING") | Q(answered_by__isnull=False, answered_at__isnull=False),
                name="response_answer_attributed",
            ),
        ]


class ChecklistIssue(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        RESOLVED = "RESOLVED", _("Resolved")

    class Source(models.TextChoices):
        REPORTED = "REPORTED", _("Reported by staff")
        FAILED_TASK = "FAILED_TASK", _("Failed task")
        TEMPERATURE = "TEMPERATURE", _("Temperature outside range")

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="checklist_issues")
    run = models.ForeignKey(ChecklistRun, on_delete=models.PROTECT, related_name="issues")
    response = models.ForeignKey(ChecklistResponse, on_delete=models.PROTECT, related_name="issues")
    source = models.CharField(max_length=16, choices=Source.choices)
    description = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    reported_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    reported_at = models.DateTimeField()
    resolved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-reported_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=~Q(description=""), name="issue_description_required"),
            models.CheckConstraint(
                condition=Q(status="OPEN", resolved_at__isnull=True)
                | (Q(status="RESOLVED", resolved_at__isnull=False, resolved_by__isnull=False) & ~Q(resolution_note="")),
                name="issue_resolution_consistent",
            ),
        ]


class IssueAttachment(models.Model):
    ALLOWED_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

    issue = models.ForeignKey(ChecklistIssue, on_delete=models.PROTECT, related_name="attachments")
    storage_key = models.CharField(max_length=80, unique=True)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=32)
    byte_size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField()

    class Meta:
        ordering = ["uploaded_at", "id"]
