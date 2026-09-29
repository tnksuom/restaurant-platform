from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from core.models import Restaurant


class PublicProfile(models.Model):
    """What customers see about the restaurant. The name comes from Restaurant.name.

    Editing screens for these details are EP08; until then they are set by the
    demo/setup command or by an administrator.
    """

    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name="public_profile")
    tagline = models.CharField(_("tagline"), max_length=140, blank=True)
    introduction = models.TextField(_("introduction"), blank=True, max_length=1200)
    street_address = models.CharField(_("street address"), max_length=150, blank=True)
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)
    city = models.CharField(_("city"), max_length=80, blank=True)
    country = models.CharField(_("country"), max_length=80, blank=True, default="Finland")
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    email = models.EmailField(_("email"), blank=True)
    logo_key = models.CharField(max_length=80, blank=True)
    hero_key = models.CharField(max_length=80, blank=True)
    hero_alt = models.CharField(_("hero image description"), max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Public profile of {self.restaurant}"

    @property
    def address_one_line(self):
        city = " ".join(p for p in (self.postal_code, self.city) if p)
        return ", ".join(p for p in (self.street_address, city, self.country) if p)

    @property
    def has_address(self):
        return bool(self.street_address and self.city)


class OpeningHours(models.Model):
    """One opening period on a weekday. No rows for a weekday means closed.

    A period whose closing time is at or before its opening time ends after
    midnight (for example 17:00–02:00).
    """

    WEEKDAYS = [(0, _("Monday")), (1, _("Tuesday")), (2, _("Wednesday")), (3, _("Thursday")),
                (4, _("Friday")), (5, _("Saturday")), (6, _("Sunday"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="opening_hours")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    opens = models.TimeField()
    closes = models.TimeField()

    class Meta:
        ordering = ["weekday", "opens"]
        constraints = [
            models.CheckConstraint(condition=Q(weekday__lte=6), name="opening_hours_weekday_valid"),
            models.CheckConstraint(condition=~Q(opens=F("closes")), name="opening_hours_not_empty"),
        ]

    @property
    def crosses_midnight(self):
        return self.closes <= self.opens


class Promotion(models.Model):
    """An announcement on the homepage between two local dates (inclusive)."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="promotions")
    title = models.CharField(_("title"), max_length=120)
    body = models.TextField(_("text"), blank=True, max_length=800)
    starts_on = models.DateField(_("first day"))
    ends_on = models.DateField(_("last day"), null=True, blank=True, help_text=_("Leave empty to keep it running."))
    is_published = models.BooleanField(_("published"), default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F("starts_on")), name="promotion_dates_ordered"
            ),
            models.CheckConstraint(condition=~Q(title=""), name="promotion_title_nonempty"),
        ]

    def __str__(self):
        return self.title

    def state_on(self, day):
        if not self.is_published:
            return "unpublished"
        if day < self.starts_on:
            return "upcoming"
        if self.ends_on and day > self.ends_on:
            return "expired"
        return "active"
