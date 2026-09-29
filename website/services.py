"""Public website rules (EP01): opening status, promotions, map link."""
from datetime import datetime, timedelta
from urllib.parse import quote

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from core import audit
from core.models import Restaurant
from core.permissions import is_manager
from core.timeutils import business_today, now

from .models import OpeningHours, PublicProfile, Promotion


def public_restaurant():
    """The single restaurant this installation serves (V1 has one location)."""
    return Restaurant.objects.order_by("pk").first()


def profile_for(restaurant):
    profile, _created = PublicProfile.objects.get_or_create(restaurant=restaurant)
    return profile


# ------------------------------------------------------------------ opening hours


def weekly_hours(restaurant):
    """Monday..Sunday, each with its list of (opens, closes) periods (empty = closed)."""
    days = {d: [] for d, _label in OpeningHours.WEEKDAYS}
    for period in OpeningHours.objects.filter(restaurant=restaurant):
        days[period.weekday].append(period)
    return [{"weekday": d, "label": str(label), "periods": days[d]} for d, label in OpeningHours.WEEKDAYS]


def _intervals_around(restaurant, local_now):
    """Concrete local (start, end) datetimes for periods starting yesterday .. in 7 days."""
    tz = local_now.tzinfo
    periods = list(OpeningHours.objects.filter(restaurant=restaurant))
    out = []
    for offset in range(-1, 8):
        day = (local_now + timedelta(days=offset)).date()
        for p in periods:
            if p.weekday != day.weekday():
                continue
            start = datetime.combine(day, p.opens, tzinfo=tz)
            end_day = day + timedelta(days=1) if p.crosses_midnight else day
            end = datetime.combine(end_day, p.closes, tzinfo=tz)
            out.append((start, end))
    return sorted(out)


def open_status(restaurant, at=None):
    """{'is_open', 'closes_at', 'next_opens_at', 'has_hours'} in the restaurant's local time.

    Wall-clock comparisons are used on purpose: opening hours are wall-clock times,
    so on a daylight-saving night the hours still mean what the sign on the door says.
    """
    at = at or now()
    local_now = at.astimezone(restaurant.tz)
    naive_now = local_now.replace(tzinfo=None)
    intervals = [(s.replace(tzinfo=None), e.replace(tzinfo=None)) for s, e in _intervals_around(restaurant, local_now)]
    status = {"is_open": False, "closes_at": None, "next_opens_at": None, "has_hours": bool(intervals)}
    for start, end in intervals:
        if start <= naive_now < end:
            status["is_open"] = True
            status["closes_at"] = end
            break
    if not status["is_open"]:
        upcoming = [s for s, _e in intervals if s > naive_now]
        status["next_opens_at"] = upcoming[0] if upcoming else None
    status["today"] = naive_now.date()
    return status


# ------------------------------------------------------------------ promotions


def active_promotions(restaurant, day=None):
    day = day or business_today(restaurant)
    return list(
        Promotion.objects.filter(restaurant=restaurant, is_published=True, starts_on__lte=day)
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=day))
        .order_by("starts_on", "id")
    )


def _require_manager(actor):
    if not is_manager(actor):
        raise PermissionDenied


def save_promotion(actor, promotion, *, title, body, starts_on, ends_on, is_published):
    _require_manager(actor)
    title = (title or "").strip()
    errors = {}
    if not title:
        errors["title"] = _("Enter a title.")
    if not starts_on:
        errors["starts_on"] = _("Choose the first day.")
    if starts_on and ends_on and ends_on < starts_on:
        errors["ends_on"] = _("The last day cannot be before the first day.")
    if errors:
        raise ValidationError(errors)
    with transaction.atomic():
        creating = promotion is None
        if creating:
            promotion = Promotion(restaurant=actor.restaurant, created_by=actor)
        else:
            promotion = Promotion.objects.select_for_update().get(pk=promotion.pk, restaurant=actor.restaurant)
        promotion.title, promotion.body = title, (body or "").strip()
        promotion.starts_on, promotion.ends_on, promotion.is_published = starts_on, ends_on, is_published
        promotion.save()
        audit.record(
            actor, "website.promotion_created" if creating else "website.promotion_updated", promotion,
            {"title": title, "starts_on": str(starts_on), "ends_on": str(ends_on) if ends_on else None,
             "is_published": is_published},
        )
    return promotion


@transaction.atomic
def delete_promotion(actor, promotion_id):
    _require_manager(actor)
    promotion = Promotion.objects.select_for_update().get(pk=promotion_id, restaurant=actor.restaurant)
    audit.record(actor, "website.promotion_deleted", promotion, {"title": promotion.title})
    promotion.delete()


# ------------------------------------------------------------------ maps


def map_url(profile):
    """A universal Google Maps search link: opens the maps app on phones, the website elsewhere."""
    if not profile.has_address:
        return ""
    return "https://www.google.com/maps/search/?api=1&query=" + quote(profile.address_one_line, safe="")
