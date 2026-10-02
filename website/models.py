from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from core.models import Restaurant


class PublicProfile(models.Model):
    """What customers see about the restaurant. The name comes from Restaurant.name.

    The owner edits these on the "Restaurant details" and "Website images" screens (EP08).
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


class Period:
    """A (opens, closes) pair; closes at or before opens means it ends after midnight."""

    __slots__ = ("opens", "closes")

    def __init__(self, opens, closes):
        self.opens, self.closes = opens, closes

    @property
    def crosses_midnight(self):
        return self.closes <= self.opens

    def __eq__(self, other):
        return isinstance(other, Period) and (self.opens, self.closes) == (other.opens, other.closes)

    def __repr__(self):
        return f"Period({self.opens:%H:%M}-{self.closes:%H:%M})"


class SpecialDay(models.Model):
    """Opening hours for one date that replace the regular weekday hours (holidays, events).

    Either closed all day, or open for one or two periods.
    """

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="special_days")
    date = models.DateField(_("date"))
    is_closed = models.BooleanField(_("closed all day"), default=False)
    opens = models.TimeField(null=True, blank=True)
    closes = models.TimeField(null=True, blank=True)
    opens_2 = models.TimeField(null=True, blank=True)
    closes_2 = models.TimeField(null=True, blank=True)
    note = models.CharField(_("note"), max_length=80, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["restaurant", "date"], name="special_day_unique_date"),
            models.CheckConstraint(
                condition=Q(is_closed=True) | (Q(opens__isnull=False) & Q(closes__isnull=False)),
                name="special_day_closed_or_hours",
            ),
            models.CheckConstraint(
                condition=(Q(opens_2__isnull=True) & Q(closes_2__isnull=True))
                | (Q(opens_2__isnull=False) & Q(closes_2__isnull=False)),
                name="special_day_second_period_complete",
            ),
        ]

    def __str__(self):
        return f"{self.date} ({self.note or ('closed' if self.is_closed else 'special hours')})"

    @property
    def periods(self):
        if self.is_closed:
            return []
        out = [Period(self.opens, self.closes)]
        if self.opens_2 is not None:
            out.append(Period(self.opens_2, self.closes_2))
        return out


class GalleryImage(models.Model):
    """A photo of the restaurant shown on the public homepage."""

    MAX_PER_RESTAURANT = 8

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="gallery_images")
    key = models.CharField(max_length=80, unique=True)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    alt = models.CharField(_("description"), max_length=200)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.alt


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
