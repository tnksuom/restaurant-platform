"""Platform rules: sign-up, locations, plans, limits, status and modules.

Views only parse input and call these. Owners act on their own organization; platform
administrators (staff.Employee.is_platform_admin) act on any organization.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import Restaurant, validate_time_zone
from staff.models import Employee, Role, normalize_identifier

from .models import MODULES, Organization, OrganizationEvent, Plan, validate_slug


def record(org, actor, action, changes=None):
    return OrganizationEvent.objects.create(organization=org, actor=actor, action=action, changes=changes or {})


def _require_owner(actor, org):
    if not (actor.is_authenticated and actor.is_active and not actor.is_platform_admin
            and actor.organization_id == org.pk and actor.role == Role.OWNER):
        raise PermissionDenied


def _require_platform_admin(actor):
    if not (actor.is_authenticated and actor.is_active and actor.is_platform_admin):
        raise PermissionDenied


def today():
    return timezone.now().date()


# ------------------------------------------------------------------ limits


def active_staff_count(org):
    return Employee.objects.filter(organization=org, is_active=True).count()


def check_can_add_staff(org, adding=1):
    limit = org.plan.max_staff
    if limit is not None and active_staff_count(org) + adding > limit:
        raise ValidationError(
            _("Your %(plan)s plan allows %(n)s active staff. Upgrade the plan or deactivate someone first.")
            % {"plan": org.plan.name, "n": limit}
        )


def check_can_add_location(org):
    if org.restaurants.count() >= org.plan.max_locations:
        raise ValidationError(
            _("Your %(plan)s plan allows %(n)s location(s). Upgrade the plan to add more.")
            % {"plan": org.plan.name, "n": org.plan.max_locations}
        )


# ------------------------------------------------------------------ sign-up


def normalize_slug(value):
    return (value or "").strip().lower()


def slug_available(slug):
    return not Organization.objects.filter(slug=slug).exists()


@transaction.atomic
def create_organization(*, name, slug, plan, location_name, owner_name, owner_login, owner_password,
                        time_zone="Europe/Helsinki", contact_email="", actor=None, status=None):
    """Create an organization, its first location and its owner. Used by sign-up and platform admins."""
    name, location_name, owner_name = (name or "").strip(), (location_name or "").strip(), (owner_name or "").strip()
    slug, login = normalize_slug(slug), normalize_identifier(owner_login)
    errors = {}
    if not name:
        errors["name"] = _("Enter the business name.")
    try:
        validate_slug(slug)
    except ValidationError as exc:
        errors["slug"] = exc.messages
    else:
        if not slug_available(slug):
            errors["slug"] = _("This address is already taken.")
    if not owner_name:
        errors["owner_name"] = _("Enter your name.")
    if not login:
        errors["owner_login"] = _("Enter a login name.")
    try:
        validate_time_zone(time_zone)
    except ValidationError as exc:
        errors["time_zone"] = exc.messages
    if plan is None or (actor is None and not plan.is_public):
        errors["plan"] = _("Choose a plan.")
    if errors:
        raise ValidationError(errors)
    status = status or Organization.Status.TRIAL
    try:
        with transaction.atomic():
            org = Organization.objects.create(
                name=name, slug=slug, plan=plan, status=status, contact_email=(contact_email or "").strip(),
                trial_ends_on=Organization.trial_end_from(today()) if status == Organization.Status.TRIAL else None,
                module_menu=plan.includes_website, module_website=plan.includes_website,
            )
    except IntegrityError:
        raise ValidationError({"slug": _("This address is already taken.")})
    location = Restaurant.objects.create(organization=org, name=location_name or name, time_zone=time_zone)
    owner = Employee.objects.create_user(location, login, owner_name, Role.OWNER, password=owner_password)
    record(org, actor or owner, "organization.created", {"plan": plan.code, "status": status, "slug": slug})
    return org, location, owner


# ------------------------------------------------------------------ locations


@transaction.atomic
def add_location(actor, name, time_zone=None):
    org = Organization.objects.select_for_update().get(pk=actor.organization_id)
    _require_owner(actor, org)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": _("Enter the location name.")})
    check_can_add_location(org)
    tz = time_zone or actor.restaurant.time_zone
    validate_time_zone(tz)
    location = Restaurant.objects.create(organization=org, name=name, time_zone=tz)
    record(org, actor, "location.added", {"location": location.pk, "name": name})
    return location


@transaction.atomic
def rename_location(actor, location_id, name):
    org = actor.organization
    _require_owner(actor, org)
    location = Restaurant.objects.select_for_update().get(pk=location_id, organization=org)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": _("Enter the location name.")})
    if name != location.name:
        record(org, actor, "location.renamed", {"location": location.pk, "before": location.name, "after": name})
        location.name = name
        location.save(update_fields=["name", "updated_at"])
    return location


def switch_location(actor, location_id):
    location = Restaurant.objects.filter(pk=location_id, organization_id=actor.organization_id).first()
    if location is None or not actor.works_at(location):
        raise PermissionDenied
    actor.restaurant = location
    actor.save(update_fields=["restaurant", "updated_at"])
    return location


@transaction.atomic
def set_staff_locations(actor, target, location_ids):
    """Owners choose which locations a person works at (at least one)."""
    _require_owner(actor, actor.organization)
    target = Employee.objects.select_for_update().get(pk=target.pk, organization_id=actor.organization_id)
    locations = list(Restaurant.objects.filter(pk__in=location_ids, organization_id=actor.organization_id))
    if not locations:
        raise ValidationError({"locations": _("Choose at least one location.")})
    before = sorted(target.locations.values_list("pk", flat=True))
    target.locations.set(locations)
    after = sorted(loc.pk for loc in locations)
    if before != after:
        record(actor.organization, actor, "employee.locations_changed",
               {"employee": target.pk, "before": before, "after": after})
    return target


# ------------------------------------------------------------------ modules and plan (owner)


@transaction.atomic
def set_modules(actor, org, enabled):
    """`enabled` is the set of module names to switch on. Modules the plan lacks stay off."""
    if not actor.is_platform_admin:
        _require_owner(actor, org)
    org = Organization.objects.select_for_update().select_related("plan").get(pk=org.pk)
    before = {m: getattr(org, f"module_{m}") for m in MODULES}
    for m in MODULES:
        setattr(org, f"module_{m}", m in enabled and org.plan.allows_module(m))
    after = {m: getattr(org, f"module_{m}") for m in MODULES}
    if before != after:
        org.save()
        record(org, actor, "organization.modules_changed", {"before": before, "after": after})
    return org


@transaction.atomic
def change_plan(actor, org, plan):
    """Owners may switch plans during the trial; afterwards a platform admin changes it."""
    org = Organization.objects.select_for_update().select_related("plan").get(pk=org.pk)
    if actor.is_platform_admin:
        pass
    else:
        _require_owner(actor, org)
        if org.status != Organization.Status.TRIAL or not plan.is_public:
            raise PermissionDenied
    if plan.pk == org.plan_id:
        return org
    if org.restaurants.count() > plan.max_locations:
        raise ValidationError(_("This plan allows %(n)s location(s), but you have %(have)s.")
                              % {"n": plan.max_locations, "have": org.restaurants.count()})
    if plan.max_staff is not None and active_staff_count(org) > plan.max_staff:
        raise ValidationError(_("This plan allows %(n)s active staff, but you have %(have)s.")
                              % {"n": plan.max_staff, "have": active_staff_count(org)})
    old = org.plan.code
    org.plan = plan
    if not plan.includes_website:
        org.module_menu = org.module_website = False
    org.save()
    record(org, actor, "organization.plan_changed", {"before": old, "after": plan.code})
    return org


# ------------------------------------------------------------------ status (platform admin)


@transaction.atomic
def set_status(actor, org, status, note=""):
    _require_platform_admin(actor)
    if status not in Organization.Status.values:
        raise ValidationError(_("Choose a valid status."))
    org = Organization.objects.select_for_update().get(pk=org.pk)
    note = (note or "").strip()
    if status in (Organization.Status.SUSPENDED, Organization.Status.CANCELLED) and not note:
        raise ValidationError({"note": _("Give a reason. The owner sees it.")})
    if status == Organization.Status.TRIAL and org.trial_ends_on is None:
        org.trial_ends_on = Organization.trial_end_from(today())
    before = org.status
    org.status, org.status_note = status, note
    org.save()
    record(org, actor, "organization.status_changed", {"before": before, "after": status, "note": note})
    return org


@transaction.atomic
def extend_trial(actor, org, until):
    _require_platform_admin(actor)
    org = Organization.objects.select_for_update().get(pk=org.pk)
    if until is None or until < today():
        raise ValidationError({"until": _("Choose today or a later date.")})
    before = org.trial_ends_on
    org.trial_ends_on, org.status = until, Organization.Status.TRIAL
    org.save()
    record(org, actor, "organization.trial_extended", {"before": str(before) if before else None, "after": str(until)})
    return org


def organization_usage(org):
    return {
        "locations": org.restaurants.count(),
        "max_locations": org.plan.max_locations,
        "staff": active_staff_count(org),
        "max_staff": org.plan.max_staff,
    }
