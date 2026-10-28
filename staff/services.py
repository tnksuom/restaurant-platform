"""Staff management rules (EP03). Views call these; tests exercise them directly."""
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from core import audit
from core.timeutils import now

from .models import Employee, Role, normalize_identifier


def can_manage(actor, target):
    """May `actor` edit/deactivate `target`?

    Same organization only. Owners manage everyone in it; managers manage the
    non-owners who work at the location they are in.
    """
    if not actor.is_active or actor.is_platform_admin or actor.organization_id != target.organization_id:
        return False
    if actor.role == Role.OWNER:
        return True
    if actor.role == Role.MANAGER:
        return target.role != Role.OWNER and target.works_at(actor.restaurant)
    return False


def assignable_roles(actor):
    if actor.role == Role.OWNER:
        return [Role.OWNER, Role.MANAGER, Role.EMPLOYEE]
    if actor.role == Role.MANAGER:
        return [Role.MANAGER, Role.EMPLOYEE]
    return []


def _require(condition):
    if not condition:
        raise PermissionDenied


def _check_identifier_free(organization, identifier, exclude_pk=None):
    qs = Employee.objects.filter(organization=organization, login_identifier__iexact=identifier)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise ValidationError({"login_identifier": _("This login name is already in use.")})


@transaction.atomic
def create_employee(actor, display_name, login_identifier, role, phone=""):
    _require(role in assignable_roles(actor))
    identifier = normalize_identifier(login_identifier)
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValidationError({"display_name": _("Enter a name.")})
    if not identifier:
        raise ValidationError({"login_identifier": _("Enter a login name.")})
    _check_identifier_free(actor.organization, identifier)
    from tenants.services import check_can_add_staff

    check_can_add_staff(actor.organization)
    try:
        with transaction.atomic():
            employee = Employee.objects.create_user(
                restaurant=actor.restaurant,
                login_identifier=identifier,
                display_name=display_name,
                role=role,
                phone=(phone or "").strip(),
            )
    except IntegrityError:
        raise ValidationError({"login_identifier": _("This login name is already in use.")})
    audit.record(actor, "employee.created", employee, {"role": role, "login_identifier": identifier})
    return employee


@transaction.atomic
def update_details(actor, target, display_name, phone):
    _require(can_manage(actor, target))
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValidationError({"display_name": _("Enter a name.")})
    before = {"display_name": target.display_name, "phone": target.phone}
    target.display_name = display_name
    target.phone = (phone or "").strip()
    target.save(update_fields=["display_name", "phone", "updated_at"])
    after = {"display_name": target.display_name, "phone": target.phone}
    if before != after:
        audit.record(actor, "employee.details_changed", target, {"before": before, "after": after})
    return target


def _lock_active_owners(organization_id):
    return list(
        Employee.objects.select_for_update()
        .filter(organization_id=organization_id, role=Role.OWNER, is_active=True)
        .order_by("pk")
    )


def _would_remove_last_owner(target):
    owners = _lock_active_owners(target.organization_id)
    return target.role == Role.OWNER and target.is_active and len(owners) <= 1


@transaction.atomic
def change_role(actor, target, new_role):
    if new_role not in Role.values:
        raise ValidationError({"role": _("Choose a valid role.")})
    target = Employee.objects.select_for_update().get(pk=target.pk)
    _require(can_manage(actor, target) and new_role in assignable_roles(actor))
    if new_role == target.role:
        return target
    if target.role == Role.OWNER and _would_remove_last_owner(target):
        raise ValidationError(_("The last active owner cannot be demoted. Make someone else owner first."))
    old = target.role
    target.role = new_role
    target.save(update_fields=["role", "updated_at"])
    audit.record(actor, "employee.role_changed", target, {"before": old, "after": new_role})
    return target


def _end_sessions(employee):
    for session in Session.objects.filter(expire_date__gt=now()).iterator():
        if session.get_decoded().get("_auth_user_id") == str(employee.pk):
            session.delete()


@transaction.atomic
def deactivate(actor, target):
    target = Employee.objects.select_for_update().get(pk=target.pk)
    _require(can_manage(actor, target))
    if target.pk == actor.pk:
        raise ValidationError(_("You cannot deactivate your own account."))
    if not target.is_active:
        return target
    if _would_remove_last_owner(target):
        raise ValidationError(_("The last active owner cannot be deactivated."))
    from timeclock.models import Shift

    if Shift.objects.filter(employee=target, status=Shift.Status.OPEN).exists():
        raise ValidationError(
            _(
                "%(name)s has an open shift. Close it with a time correction (with a reason) "
                "before deactivating the account."
            )
            % {"name": target.display_name}
        )
    target.is_active = False
    target.deactivated_at = now()
    target.save(update_fields=["is_active", "deactivated_at", "updated_at"])
    audit.record(actor, "employee.deactivated", target)
    transaction.on_commit(lambda: _end_sessions(target))
    return target


@transaction.atomic
def reactivate(actor, target):
    target = Employee.objects.select_for_update().get(pk=target.pk)
    _require(can_manage(actor, target))
    if target.is_active:
        return target
    from tenants.services import check_can_add_staff

    check_can_add_staff(target.organization)
    target.is_active = True
    target.deactivated_at = None
    target.save(update_fields=["is_active", "deactivated_at", "updated_at"])
    audit.record(actor, "employee.reactivated", target)
    return target


def issue_setup_link(actor, target, request=None):
    """One-time link for the staff member to set (or reset) their own password.

    The link is shown to the manager to hand over in person or by message.
    No password is ever generated, stored in plain text or emailed.
    """
    _require(can_manage(actor, target) and target.is_active)
    uid = urlsafe_base64_encode(force_bytes(target.pk))
    token = default_token_generator.make_token(target)
    path = reverse("set_password", args=[uid, token])
    from django.conf import settings

    if not settings.TENANT_SUBDOMAINS:  # single-domain hosts: the link has to say which workspace
        path += f"?workspace={target.organization.slug}"
    audit.record(actor, "employee.setup_link_issued", target)
    return request.build_absolute_uri(path) if request else path
