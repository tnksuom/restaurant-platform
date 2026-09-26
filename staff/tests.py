from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditEvent
from core.testing import PASSWORD, frozen, local, make_employee, make_staff
from timeclock import services as time_services

from . import services
from .models import Employee, Role

GENERIC_ERROR = "Those login details did not work"


class LoginTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def login(self, username, password=PASSWORD):
        return self.client.post(reverse("login"), {"username": username, "password": password}, follow=True)

    def test_valid_login_logout_and_case_insensitive_identifier(self):
        resp = self.login("ANNA")
        self.assertEqual(resp.context["user"], self.anna)
        self.assertContains(resp, "Hello, Anna")
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("today")).status_code, 302)

    def test_invalid_credentials_are_generic(self):
        wrong_pw = self.login("anna", "nope")
        unknown = self.login("nobody", "nope")
        self.anna.is_active = False
        self.anna.save()
        disabled = self.login("anna")
        for resp in (wrong_pw, unknown, disabled):
            self.assertFalse(resp.context["user"].is_authenticated)
            self.assertContains(resp, GENERIC_ERROR)
            self.assertNotContains(resp, "inactive")

    def test_unauthenticated_requests_are_denied(self):
        for name in ["today", "staff:list", "inventory:list", "checklists:today", "timeclock:clock", "timeclock:mine"]:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn(reverse("login"), resp["Location"])
        resp = self.client.post(reverse("timeclock:action"), {"action": "clock_in"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(time_services.open_shift(self.anna))

    def test_deactivation_ends_existing_session(self):
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("today")).status_code, 200)
        services.deactivate(self.manager, self.anna)
        resp = self.client.get(reverse("today"))
        self.assertEqual(resp.status_code, 302)
        resp = self.client.post(reverse("timeclock:action"), {"action": "clock_in"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(time_services.open_shift(self.anna))


class PermissionMatrixTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_employee_cannot_use_manager_pages(self):
        self.client.force_login(self.anna)
        urls = [
            reverse("staff:list"), reverse("staff:create"), reverse("staff:edit", args=[self.ben.pk]),
            reverse("staff:role", args=[self.ben.pk]), reverse("staff:deactivate", args=[self.ben.pk]),
            reverse("inventory:create"), reverse("inventory:history"), reverse("checklists:templates"),
            reverse("checklists:template_create"), reverse("checklists:review"), reverse("checklists:issues"),
            reverse("timeclock:timesheets"),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertContains(self.client.get(urls[0]), "have access to this page", status_code=403)
        resp = self.client.post(
            reverse("staff:create"), {"display_name": "X", "login_identifier": "x", "role": "OWNER"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Employee.objects.filter(login_identifier="x").exists())

    def test_employee_sees_only_own_profile(self):
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("staff:detail", args=[self.anna.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("staff:detail", args=[self.ben.pk])).status_code, 404)

    def test_manager_cannot_create_or_manage_owner(self):
        with self.assertRaises(PermissionDenied):
            services.create_employee(self.manager, "Boss", "boss", Role.OWNER)
        with self.assertRaises(PermissionDenied):
            services.update_details(self.manager, self.owner, "Renamed", "")
        with self.assertRaises(PermissionDenied):
            services.deactivate(self.manager, self.owner)
        with self.assertRaises(PermissionDenied):
            services.change_role(self.manager, self.anna, Role.OWNER)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("staff:edit", args=[self.owner.pk])).status_code, 404)

    def test_manager_can_switch_employee_and_manager(self):
        services.change_role(self.manager, self.anna, Role.MANAGER)
        services.change_role(self.manager, self.anna, Role.EMPLOYEE)
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.role, Role.EMPLOYEE)

    def test_role_change_applies_to_signed_in_session(self):
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("staff:list")).status_code, 403)
        services.change_role(self.owner, self.anna, Role.MANAGER)
        self.assertEqual(self.client.get(reverse("staff:list")).status_code, 200)
        services.change_role(self.owner, self.anna, Role.EMPLOYEE)
        self.assertEqual(self.client.get(reverse("staff:list")).status_code, 403)

    def test_form_cannot_spoof_restaurant_or_role(self):
        self.client.force_login(self.manager)
        self.client.post(
            reverse("staff:create"),
            {"display_name": "Cara", "login_identifier": "cara", "role": "OWNER", "restaurant": 999},
        )
        self.assertFalse(Employee.objects.filter(login_identifier="cara").exists())  # OWNER not an allowed choice


class EmployeeLifecycleTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_create_rejects_duplicate_identifier_any_case(self):
        services.create_employee(self.manager, "Cara", "Cara@Example.com", Role.EMPLOYEE)
        with self.assertRaises(ValidationError):
            services.create_employee(self.manager, "Cara 2", "cara@example.COM", Role.EMPLOYEE)

    def test_new_employee_sets_own_password_via_one_time_link(self):
        emp = services.create_employee(self.manager, "Cara", "cara", Role.EMPLOYEE)
        self.assertFalse(emp.has_usable_password())
        self.assertFalse(self.client.login(username="cara", password=""))
        path = services.issue_setup_link(self.manager, emp)
        resp = self.client.post(path, {"new_password1": "Kitchen-Door-77", "new_password2": "Kitchen-Door-77"})
        self.assertRedirects(resp, reverse("login"), fetch_redirect_response=False)
        self.assertTrue(self.client.login(username="cara", password="Kitchen-Door-77"))
        self.client.logout()
        # the link is single-use
        self.assertEqual(self.client.get(path).status_code, 400)

    def test_created_account_response_has_no_password(self):
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse("staff:create"), {"display_name": "Dan", "login_identifier": "dan", "role": "EMPLOYEE"}
        )
        self.assertContains(resp, "/set-password/")
        dan = Employee.objects.get(login_identifier="dan")
        self.assertNotContains(resp, dan.password)

    def test_name_change_keeps_same_id_and_is_audited(self):
        pk = self.anna.pk
        services.update_details(self.manager, self.anna, "Anna K.", "040 123")
        self.anna.refresh_from_db()
        self.assertEqual((self.anna.pk, self.anna.display_name), (pk, "Anna K."))
        self.assertTrue(AuditEvent.objects.filter(action="employee.details_changed", entity_id=str(pk)).exists())

    def test_last_active_owner_is_protected(self):
        with self.assertRaises(ValidationError):
            services.change_role(self.owner, self.owner, Role.MANAGER)
        second = make_employee(self.r, "owner2", Role.OWNER)
        with self.assertRaises(ValidationError):  # cannot deactivate self
            services.deactivate(self.owner, self.owner)
        services.deactivate(second, self.owner)
        with self.assertRaises(ValidationError):
            services.change_role(second, second, Role.MANAGER)

    def test_open_shift_blocks_deactivation(self):
        time_services.clock_in(self.anna)
        with self.assertRaises(ValidationError) as ctx:
            services.deactivate(self.manager, self.anna)
        self.assertIn("open shift", str(ctx.exception))
        self.anna.refresh_from_db()
        self.assertTrue(self.anna.is_active)

    def test_deactivate_and_reactivate_are_audited_and_history_kept(self):
        with frozen(local(2026, 9, 1, 9, 0)):
            shift = time_services.clock_in(self.anna)
        with frozen(local(2026, 9, 1, 17, 0)):
            time_services.clock_out(self.anna)
        services.deactivate(self.manager, self.anna)
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("timeclock:timesheets") + "?from=2026-09-01&to=2026-09-01")
        self.assertContains(resp, "Anna")
        self.assertContains(resp, "8.00 h")
        services.reactivate(self.manager, self.anna)
        actions = list(AuditEvent.objects.filter(entity_id=str(self.anna.pk)).values_list("action", flat=True))
        self.assertIn("employee.deactivated", actions)
        self.assertIn("employee.reactivated", actions)
        shift.refresh_from_db()
        self.assertEqual(shift.employee_id, self.anna.pk)


class AuditImmutabilityTests(TestCase):
    def test_audit_rows_cannot_be_changed(self):
        r, owner, *_ = make_staff()
        event = AuditEvent.objects.first()
        if event is None:
            from core import audit

            event = audit.record(owner, "test", owner)
        with self.assertRaises(TypeError):
            event.save()
        with self.assertRaises(TypeError):
            AuditEvent.objects.all().delete()
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE core_auditevent SET action = 'x'")
