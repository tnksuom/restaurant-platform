from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


def validate_time_zone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError(_("Unknown time zone: %(tz)s"), params={"tz": value})


def default_time_zone():
    return settings.DEFAULT_RESTAURANT_TIME_ZONE


class Restaurant(models.Model):
    """One location. V1 has exactly one, but operational rows carry restaurant_id."""

    name = models.CharField(max_length=200)
    time_zone = models.CharField(max_length=64, default=default_time_zone, validators=[validate_time_zone])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def tz(self):
        return ZoneInfo(self.time_zone)


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError(f"{self.model.__name__} rows are append-only")

    def delete(self):
        raise TypeError(f"{self.model.__name__} rows are append-only")


class AppendOnlyModel(models.Model):
    """Rows can be inserted but never updated or deleted.

    Enforced here for application code and again by a PostgreSQL trigger
    (see core.db.append_only_trigger_sql) for anything that bypasses the ORM.
    """

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError(f"{type(self).__name__} rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} rows are append-only")


class AuditEvent(AppendOnlyModel):
    """Application-level audit history for staff and other sensitive changes."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_actions"
    )
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self):
        return f"{self.action} {self.entity_type}#{self.entity_id}"
