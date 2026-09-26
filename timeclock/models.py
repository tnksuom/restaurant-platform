from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from core.models import AppendOnlyModel, Restaurant

User = settings.AUTH_USER_MODEL


class Shift(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        CLOSED = "CLOSED", _("Closed")

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="shifts")
    employee = models.ForeignKey(User, on_delete=models.PROTECT, related_name="shifts")
    clock_in_at = models.DateTimeField()
    clock_out_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    is_corrected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-clock_in_at", "-id"]
        indexes = [models.Index(fields=["employee", "clock_in_at"]), models.Index(fields=["restaurant", "clock_in_at"])]
        constraints = [
            models.UniqueConstraint(fields=["employee"], condition=Q(status="OPEN"), name="one_open_shift_per_employee"),
            models.CheckConstraint(
                condition=Q(status="OPEN", clock_out_at__isnull=True)
                | Q(status="CLOSED", clock_out_at__isnull=False, clock_out_at__gt=F("clock_in_at")),
                name="shift_status_matches_clock_out",
            ),
        ]

    def __str__(self):
        return f"{self.employee} {self.clock_in_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self):
        return self.status == self.Status.OPEN


class Break(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="breaks")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["shift"], condition=Q(end_at__isnull=True), name="one_open_break_per_shift"),
            models.CheckConstraint(condition=Q(end_at__isnull=True) | Q(end_at__gt=F("start_at")), name="break_end_after_start"),
        ]

    @property
    def seconds(self):
        return int((self.end_at - self.start_at).total_seconds()) if self.end_at else 0


class ShiftEvent(AppendOnlyModel):
    """Immutable record of each clock action exactly as it happened."""

    class Type(models.TextChoices):
        CLOCK_IN = "CLOCK_IN", _("Clocked in")
        BREAK_START = "BREAK_START", _("Break started")
        BREAK_END = "BREAK_END", _("Break ended")
        CLOCK_OUT = "CLOCK_OUT", _("Clocked out")

    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="events")
    type = models.CharField(max_length=16, choices=Type.choices)
    at = models.DateTimeField()
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["at", "id"]


class TimeCorrection(AppendOnlyModel):
    """Append-only manager correction with full before/after snapshots."""

    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="corrections")
    changed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField()
    reason = models.TextField()
    before = models.JSONField()
    after = models.JSONField()

    class Meta:
        ordering = ["-changed_at", "-id"]
        constraints = [models.CheckConstraint(condition=~Q(reason=""), name="correction_reason_required")]
