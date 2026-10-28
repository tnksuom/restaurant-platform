"""The platform layer: plans, customer organizations and their history.

An Organization is one customer of the platform (a restaurant business). It owns one or
more locations (core.Restaurant) and its staff (staff.Employee). Every operational row
already carries restaurant_id, so data is separated per location, and locations are
separated per organization.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import AppendOnlyModel

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
RESERVED_SLUGS = {
    "www", "app", "api", "admin", "platform", "static", "media", "mail", "help", "support", "status",
    "login", "signup", "demo", "test", "dev", "staging",
}

# Modules an organization can switch on or off. Staff, dashboard and settings are always on.
MODULES = ["timeclock", "checklists", "inventory", "menu", "website"]
WEBSITE_MODULES = {"menu", "website"}  # need a plan that includes the public website


def validate_slug(value):
    if not SLUG_RE.match(value or ""):
        raise ValidationError(
            _("Use 3–40 lowercase letters, numbers and hyphens, starting and ending with a letter or number.")
        )
    if value in RESERVED_SLUGS:
        raise ValidationError(_("This address is reserved. Choose another one."))


class Plan(models.Model):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(_("name"), max_length=60)
    monthly_price = models.DecimalField(_("price per month (€)"), max_digits=8, decimal_places=2)
    max_locations = models.PositiveIntegerField(_("locations"))
    max_staff = models.PositiveIntegerField(_("active staff"), null=True, blank=True, help_text=_("Empty = no limit"))
    includes_website = models.BooleanField(_("public website and menu"), default=False)
    is_public = models.BooleanField(default=True, help_text="Offered on the sign-up page")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "monthly_price"]

    def __str__(self):
        return self.name

    def allows_module(self, module):
        return module not in WEBSITE_MODULES or self.includes_website


class Organization(models.Model):
    class Status(models.TextChoices):
        TRIAL = "TRIAL", _("Trial")
        ACTIVE = "ACTIVE", _("Active")
        SUSPENDED = "SUSPENDED", _("Suspended")
        CANCELLED = "CANCELLED", _("Cancelled")

    name = models.CharField(_("business name"), max_length=120)
    slug = models.CharField(_("web address"), max_length=40, unique=True, validators=[validate_slug])
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="organizations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TRIAL)
    trial_ends_on = models.DateField(null=True, blank=True)
    status_note = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(_("contact email"), blank=True)
    module_timeclock = models.BooleanField(_("Time clock"), default=True)
    module_checklists = models.BooleanField(_("Checklists"), default=True)
    module_inventory = models.BooleanField(_("Inventory"), default=True)
    module_menu = models.BooleanField(_("Menu"), default=False)
    module_website = models.BooleanField(_("Public website"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="TRIAL") | Q(trial_ends_on__isnull=False), name="organization_trial_has_end"
            ),
        ]

    def __str__(self):
        return self.name

    def module_enabled(self, module):
        return bool(getattr(self, f"module_{module}")) and self.plan.allows_module(module)

    def enabled_modules(self):
        return {m: self.module_enabled(m) for m in MODULES}

    def is_usable_on(self, day):
        """May staff work in the app on this (UTC) date?"""
        if self.status == self.Status.ACTIVE:
            return True
        return self.status == self.Status.TRIAL and self.trial_ends_on is not None and day <= self.trial_ends_on

    def trial_days_left(self, day):
        if self.status != self.Status.TRIAL or self.trial_ends_on is None:
            return None
        return max((self.trial_ends_on - day).days, 0)

    def primary_location(self):
        return self.restaurants.order_by("pk").first()

    @staticmethod
    def trial_end_from(day):
        return day + timedelta(days=settings.TRIAL_DAYS)


class OrganizationEvent(AppendOnlyModel):
    """Append-only history of platform-level changes (sign-up, plan, status, modules, locations)."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    action = models.CharField(max_length=64)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.organization} {self.action}"
