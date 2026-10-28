"""Public website rules: opening status, promotions, map link (EP01) and the owner's
restaurant details, opening hours, special hours and site images (EP08)."""
import re
from datetime import datetime, timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from core import audit
from core.models import Restaurant
from core.images import ImageStore, prepare_image
from core.permissions import is_manager, is_owner
from core.timeutils import business_today, now

from .models import GalleryImage, OpeningHours, Period, PublicProfile, Promotion, SpecialDay


def public_restaurant(request):
    """The location whose public website this request shows: the organization's first location."""
    tenant = getattr(request, "tenant", None)
    return tenant.primary_location() if tenant is not None else None


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


def periods_on(restaurant, day, weekly=None, specials=None):
    """The opening periods that start on a local date. A special day replaces the weekday hours."""
    if specials is None:
        special = SpecialDay.objects.filter(restaurant=restaurant, date=day).first()
    else:
        special = specials.get(day)
    if special is not None:
        return special.periods
    if weekly is None:
        weekly = list(OpeningHours.objects.filter(restaurant=restaurant))
    return [Period(p.opens, p.closes) for p in weekly if p.weekday == day.weekday()]


def _intervals_around(restaurant, local_now):
    """Concrete local (start, end) datetimes for periods starting yesterday .. in 7 days."""
    tz = local_now.tzinfo
    today = local_now.date()
    weekly = list(OpeningHours.objects.filter(restaurant=restaurant))
    specials = {
        d.date: d for d in SpecialDay.objects.filter(
            restaurant=restaurant, date__gte=today - timedelta(days=1), date__lte=today + timedelta(days=7))
    }
    out = []
    for offset in range(-1, 8):
        day = today + timedelta(days=offset)
        for p in periods_on(restaurant, day, weekly, specials):
            start = datetime.combine(day, p.opens, tzinfo=tz)
            end_day = day + timedelta(days=1) if p.crosses_midnight else day
            end = datetime.combine(end_day, p.closes, tzinfo=tz)
            out.append((start, end))
    return sorted(out)


def upcoming_special_days(restaurant, today=None, days=60):
    today = today or business_today(restaurant)
    return list(SpecialDay.objects.filter(restaurant=restaurant, date__gte=today, date__lte=today + timedelta(days=days)))


def open_status(restaurant, at=None):
    """{'is_open', 'closes_at', 'next_opens_at', 'has_hours'} in the restaurant's local time.

    Wall-clock comparisons are used on purpose: opening hours are wall-clock times,
    so on a daylight-saving night the hours still mean what the sign on the door says.
    """
    at = at or now()
    local_now = at.astimezone(restaurant.tz)
    naive_now = local_now.replace(tzinfo=None)
    intervals = [(s.replace(tzinfo=None), e.replace(tzinfo=None)) for s, e in _intervals_around(restaurant, local_now)]
    has_hours = bool(intervals) or OpeningHours.objects.filter(restaurant=restaurant).exists()
    status = {"is_open": False, "closes_at": None, "next_opens_at": None, "has_hours": has_hours}
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


# ------------------------------------------------------------------ EP08: owner's restaurant administration


def _require_owner(actor):
    if not is_owner(actor):
        raise PermissionDenied


PROFILE_FIELDS = ("tagline", "introduction", "street_address", "postal_code", "city", "country", "phone", "email")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{4,38}$")


def _profile_snapshot(restaurant, profile):
    data = {"name": restaurant.name}
    data.update({f: getattr(profile, f) for f in PROFILE_FIELDS})
    return data


@transaction.atomic
def save_restaurant_info(actor, **data):
    """US-08.01: name, introduction, address and contact details shown on the public site."""
    _require_owner(actor)
    restaurant = Restaurant.objects.select_for_update().get(pk=actor.restaurant_id)
    profile = profile_for(restaurant)
    profile = PublicProfile.objects.select_for_update().get(pk=profile.pk)
    clean = {k: " ".join(str(data.get(k) or "").split()) for k in ("name",) + PROFILE_FIELDS if k != "introduction"}
    clean["introduction"] = str(data.get("introduction") or "").strip().replace("\r\n", "\n")
    clean["email"] = clean["email"].lower()
    errors = {}
    if not clean["name"]:
        errors["name"] = _("Enter the restaurant name.")
    if clean["phone"] and not PHONE_RE.match(clean["phone"]):
        errors["phone"] = _("Enter a phone number with digits, spaces and an optional + at the start.")
    if clean["email"]:
        try:
            validate_email(clean["email"])
        except ValidationError:
            errors["email"] = _("Enter a valid email address.")
    if bool(clean["street_address"]) != bool(clean["city"]):
        errors["city" if clean["street_address"] else "street_address"] = _(
            "Enter both the street address and the city, or leave both empty."
        )
    if errors:
        raise ValidationError(errors)
    before = _profile_snapshot(restaurant, profile)
    restaurant.name = clean.pop("name")
    for field, value in clean.items():
        setattr(profile, field, value)
    restaurant.full_clean()
    profile.full_clean(exclude=["restaurant"])
    restaurant.save()
    profile.save()
    after = _profile_snapshot(restaurant, profile)
    changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
    if changed:
        audit.record(actor, "website.info_updated", profile, changed)
    return profile


def _period_list(pairs, label):
    """Validate the periods of one day. Returns sorted Period objects; raises a message string."""
    periods = []
    for opens, closes in pairs:
        if opens is None and closes is None:
            continue
        if opens is None or closes is None:
            raise ValueError(_("%(day)s: enter both the opening and the closing time.") % {"day": label})
        if opens == closes:
            raise ValueError(_("%(day)s: the closing time must differ from the opening time.") % {"day": label})
        periods.append(Period(opens, closes))
    if len(periods) > 2:
        raise ValueError(_("%(day)s: at most two opening periods per day.") % {"day": label})
    periods.sort(key=lambda p: p.opens)
    for first, second in zip(periods, periods[1:]):
        if first.crosses_midnight or first.closes > second.opens:
            raise ValueError(_("%(day)s: the two opening periods overlap.") % {"day": label})
    return periods


def _hours_snapshot(restaurant):
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return {names[day["weekday"]]: [f"{p.opens:%H:%M}-{p.closes:%H:%M}" for p in day["periods"]]
            for day in weekly_hours(restaurant)}


@transaction.atomic
def save_weekly_hours(actor, week):
    """US-08.02: `week` maps weekday 0..6 to a list of (opens, closes). An empty list means closed."""
    _require_owner(actor)
    restaurant = Restaurant.objects.select_for_update().get(pk=actor.restaurant_id)
    labels = dict(OpeningHours.WEEKDAYS)
    errors, clean = {}, {}
    for weekday in range(7):
        try:
            clean[weekday] = _period_list(week.get(weekday, []), labels[weekday])
        except ValueError as exc:
            errors[f"day_{weekday}"] = str(exc)
    if not errors:
        for weekday in range(7):
            late = clean[weekday][-1] if clean[weekday] else None
            following = clean[(weekday + 1) % 7]
            if late and late.crosses_midnight and following and late.closes > following[0].opens:
                errors[f"day_{weekday}"] = _(
                    "%(day)s: the evening period runs past the opening time on %(next)s."
                ) % {"day": labels[weekday], "next": labels[(weekday + 1) % 7]}
    if errors:
        raise ValidationError(errors)
    before = _hours_snapshot(restaurant)
    OpeningHours.objects.filter(restaurant=restaurant).delete()
    OpeningHours.objects.bulk_create([
        OpeningHours(restaurant=restaurant, weekday=weekday, opens=p.opens, closes=p.closes)
        for weekday in range(7) for p in clean[weekday]
    ])
    after = _hours_snapshot(restaurant)
    if before != after:
        audit.record(actor, "website.hours_updated", profile_for(restaurant), {"before": before, "after": after})


def _special_snapshot(day):
    return {"date": str(day.date), "closed": day.is_closed, "note": day.note,
            "periods": [f"{p.opens:%H:%M}-{p.closes:%H:%M}" for p in day.periods]}


def save_special_day(actor, special, *, date, is_closed, note, periods):
    """US-08.03: create (special=None) or update the hours for one date. `periods` is a list of (opens, closes)."""
    _require_owner(actor)
    note = " ".join((note or "").split())
    errors = {}
    today = business_today(actor.restaurant)
    if not date:
        errors["date"] = _("Choose the date.")
    elif date < today:
        errors["date"] = _("Choose today or a later date.")
    clean = []
    if not is_closed:
        try:
            clean = _period_list(periods, _("Hours"))
        except ValueError as exc:
            errors["periods"] = str(exc)
        else:
            if not clean:
                errors["periods"] = _("Enter the opening hours, or tick “Closed all day”.")
    if errors:
        raise ValidationError(errors)
    with transaction.atomic():
        creating = special is None
        if creating:
            special = SpecialDay(restaurant=actor.restaurant)
            before = None
        else:
            special = SpecialDay.objects.select_for_update().get(pk=special.pk, restaurant=actor.restaurant)
            before = _special_snapshot(special)
        clash = SpecialDay.objects.filter(restaurant=actor.restaurant, date=date).exclude(pk=special.pk).exists()
        if clash:
            raise ValidationError({"date": _("This date already has special hours. Edit that entry instead.")})
        special.date, special.is_closed, special.note = date, bool(is_closed), note
        first = clean[0] if clean else None
        second = clean[1] if len(clean) > 1 else None
        special.opens, special.closes = (first.opens, first.closes) if first else (None, None)
        special.opens_2, special.closes_2 = (second.opens, second.closes) if second else (None, None)
        special.save()
        after = _special_snapshot(special)
        if creating:
            audit.record(actor, "website.special_day_created", special, after)
        elif before != after:
            audit.record(actor, "website.special_day_updated", special, {"before": before, "after": after})
    return special


@transaction.atomic
def delete_special_day(actor, special_id):
    _require_owner(actor)
    special = SpecialDay.objects.select_for_update().get(pk=special_id, restaurant=actor.restaurant)
    audit.record(actor, "website.special_day_deleted", special, _special_snapshot(special))
    special.delete()


# ------------------------------------------------------------------ EP08: site images


SITE_IMAGE_SIDES = {"logo": 512, "hero": 1600, "gallery": 1600}


def site_store():
    return ImageStore(settings.PUBLIC_UPLOAD_ROOT)


def _prepare_site_image(upload, slot):
    return prepare_image(upload, max_bytes=settings.MENU_PHOTO_MAX_BYTES, max_side=SITE_IMAGE_SIDES[slot], field="photo")


def _clean_alt(alt, required):
    alt = " ".join((alt or "").split())
    if required and not alt:
        raise ValidationError({"alt": _("Describe the photo in a few words for people who cannot see it.")})
    return alt


def _store_new(prepared):
    store = site_store()
    return store, store.write(prepared.data, prepared.ext)


def replace_site_image(actor, slot, upload, alt=""):
    """US-08.04: upload or replace the logo or the large homepage photo. Validated before anything changes."""
    _require_owner(actor)
    if slot not in ("logo", "hero"):
        raise ValueError(slot)
    alt = _clean_alt(alt, required=(slot == "hero"))
    prepared = _prepare_site_image(upload, slot)
    store, new_key = _store_new(prepared)
    try:
        with transaction.atomic():
            profile = PublicProfile.objects.select_for_update().get(pk=profile_for(actor.restaurant).pk)
            old_key = getattr(profile, f"{slot}_key")
            setattr(profile, f"{slot}_key", new_key)
            if slot == "hero":
                profile.hero_alt = alt
            profile.save()
            audit.record(actor, f"website.{slot}_replaced" if old_key else f"website.{slot}_added", profile,
                         {"alt": alt} if slot == "hero" else {})
    except Exception:
        store.delete(new_key)
        raise
    if old_key:
        transaction.on_commit(lambda: store.delete(old_key))
    return profile


@transaction.atomic
def set_hero_alt(actor, alt):
    _require_owner(actor)
    alt = _clean_alt(alt, required=True)
    profile = PublicProfile.objects.select_for_update().get(pk=profile_for(actor.restaurant).pk)
    if profile.hero_alt != alt:
        audit.record(actor, "website.hero_described", profile, {"before": profile.hero_alt, "after": alt})
        profile.hero_alt = alt
        profile.save()
    return profile


@transaction.atomic
def remove_site_image(actor, slot):
    _require_owner(actor)
    if slot not in ("logo", "hero"):
        raise ValueError(slot)
    profile = PublicProfile.objects.select_for_update().get(pk=profile_for(actor.restaurant).pk)
    old_key = getattr(profile, f"{slot}_key")
    if not old_key:
        return profile
    setattr(profile, f"{slot}_key", "")
    if slot == "hero":
        profile.hero_alt = ""
    profile.save()
    audit.record(actor, f"website.{slot}_removed", profile)
    transaction.on_commit(lambda: site_store().delete(old_key))
    return profile


def add_gallery_image(actor, upload, alt):
    _require_owner(actor)
    alt = _clean_alt(alt, required=True)
    if GalleryImage.objects.filter(restaurant=actor.restaurant).count() >= GalleryImage.MAX_PER_RESTAURANT:
        raise ValidationError({"photo": _("The gallery holds at most %(n)s photos. Remove one first.")
                               % {"n": GalleryImage.MAX_PER_RESTAURANT}})
    prepared = _prepare_site_image(upload, "gallery")
    store, key = _store_new(prepared)
    try:
        with transaction.atomic():
            Restaurant.objects.select_for_update().get(pk=actor.restaurant_id)  # serialise the count check
            if GalleryImage.objects.filter(restaurant=actor.restaurant).count() >= GalleryImage.MAX_PER_RESTAURANT:
                raise ValidationError({"photo": _("The gallery holds at most %(n)s photos. Remove one first.")
                                       % {"n": GalleryImage.MAX_PER_RESTAURANT}})
            last = GalleryImage.objects.filter(restaurant=actor.restaurant).order_by("-position").first()
            image = GalleryImage.objects.create(
                restaurant=actor.restaurant, key=key, width=prepared.width, height=prepared.height, alt=alt,
                position=(last.position + 1) if last else 0,
            )
            audit.record(actor, "website.gallery_added", image, {"alt": alt})
    except Exception:
        store.delete(key)
        raise
    return image


@transaction.atomic
def describe_gallery_image(actor, image_id, alt):
    _require_owner(actor)
    alt = _clean_alt(alt, required=True)
    image = GalleryImage.objects.select_for_update().get(pk=image_id, restaurant=actor.restaurant)
    if image.alt != alt:
        audit.record(actor, "website.gallery_described", image, {"before": image.alt, "after": alt})
        image.alt = alt
        image.save(update_fields=["alt"])
    return image


@transaction.atomic
def move_gallery_image(actor, image_id, direction):
    _require_owner(actor)
    images = list(GalleryImage.objects.select_for_update().filter(restaurant=actor.restaurant))
    index = next((i for i, img in enumerate(images) if img.pk == int(image_id)), None)
    if index is None:
        raise GalleryImage.DoesNotExist
    other = index - 1 if direction == "up" else index + 1
    if 0 <= other < len(images):
        images[index], images[other] = images[other], images[index]
        for position, img in enumerate(images):
            if img.position != position:
                img.position = position
                img.save(update_fields=["position"])


@transaction.atomic
def delete_gallery_image(actor, image_id):
    _require_owner(actor)
    image = GalleryImage.objects.select_for_update().get(pk=image_id, restaurant=actor.restaurant)
    key = image.key
    audit.record(actor, "website.gallery_removed", image, {"alt": image.alt})
    image.delete()
    transaction.on_commit(lambda: site_store().delete(key))


def is_public_site_image(key, organization):
    return (
        PublicProfile.objects.filter(Q(logo_key=key) | Q(hero_key=key), restaurant__organization=organization).exists()
        or GalleryImage.objects.filter(key=key, restaurant__organization=organization).exists()
    )
