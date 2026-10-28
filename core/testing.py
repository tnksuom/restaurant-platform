"""Shared test helpers."""
from contextlib import contextmanager
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.utils.text import slugify

from core.models import Restaurant
from staff.models import Employee, Role
from tenants.models import Organization, Plan

HEL = ZoneInfo("Europe/Helsinki")
PASSWORD = "Correct-Horse-42"


def make_organization(name="Test Bistro", slug=None, plan="business", status=Organization.Status.ACTIVE, **extra):
    """An organization with every module on (the Business plan includes the website)."""
    from tenants.plans import ensure_default_plans

    ensure_default_plans(Plan)  # TransactionTestCase empties the table between tests
    defaults = {"module_menu": True, "module_website": True}
    defaults.update(extra)
    if status == Organization.Status.TRIAL:
        defaults.setdefault("trial_ends_on", Organization.trial_end_from(datetime.now(HEL).date()))
    return Organization.objects.create(
        name=name, slug=slug or slugify(name), plan=Plan.objects.get(code=plan), status=status, **defaults
    )


def make_restaurant(name="Test Bistro", organization=None):
    """A location. Without an organization it gets its own one, named and addressed after it."""
    organization = organization or make_organization(name)
    return Restaurant.objects.create(organization=organization, name=name, time_zone="Europe/Helsinki")


def make_employee(restaurant, login, role=Role.EMPLOYEE, name=None, password=PASSWORD, active=True):
    emp = Employee.objects.create_user(
        restaurant=restaurant, login_identifier=login, display_name=name or login.title(), role=role, password=password
    )
    if not active:
        emp.is_active = False
        emp.save()
    return emp


def make_staff():
    r = make_restaurant()
    return (
        r,
        make_employee(r, "owner", Role.OWNER),
        make_employee(r, "manager", Role.MANAGER),
        make_employee(r, "anna", Role.EMPLOYEE),
        make_employee(r, "ben", Role.EMPLOYEE),
    )


def local(*args):
    """Aware datetime in Helsinki local time."""
    return datetime(*args, tzinfo=HEL)


@contextmanager
def frozen(at):
    """Freeze server time (core.timeutils.now() reads django.utils.timezone.now)."""
    with mock.patch("django.utils.timezone.now", return_value=at):
        yield
