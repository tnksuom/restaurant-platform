"""Shared test helpers."""
from contextlib import contextmanager
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from core.models import Restaurant
from staff.models import Employee, Role

HEL = ZoneInfo("Europe/Helsinki")
PASSWORD = "Correct-Horse-42"


def make_restaurant(name="Test Bistro"):
    return Restaurant.objects.create(name=name, time_zone="Europe/Helsinki")


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
