"""Render every screen for every role with realistic data, so template errors surface in tests."""
from django.test import TestCase
from django.urls import reverse

from checklists import services as checklist_services
from checklists.models import ChecklistIssue, ResponseStatus
from checklists.tests import make_template, responses
from core.testing import frozen, local, make_staff
from inventory.models import MovementType
from inventory import services as inventory_services
from timeclock import services as time_services


class SmokeTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.item = inventory_services.create_item(
            self.manager, name="Flour", category="ingredient", unit="kg", opening_quantity="5", minimum_quantity="5"
        )
        inventory_services.post_movement(self.anna, self.item.pk, MovementType.WASTE, "1", waste_reason="SPILLED")
        self.template = make_template(self.manager)
        self.run, _ = checklist_services.start_run(self.anna, self.template.pk)
        rs = responses(self.run)
        checklist_services.save_response(self.anna, rs["Fridge 1"].pk, ResponseStatus.PASS, temperature="7")
        self.issue = ChecklistIssue.objects.get()
        with frozen(local(2026, 9, 1, 9)):
            self.shift = time_services.clock_in(self.anna)
        with frozen(local(2026, 9, 1, 17)):
            time_services.clock_out(self.anna)
        time_services.clock_in(self.anna)

    def urls(self, manager):
        common = [
            reverse("today"), reverse("staff:me"), reverse("staff:change_password"),
            reverse("inventory:list"), reverse("inventory:detail", args=[self.item.pk]),
            reverse("checklists:today"), reverse("checklists:run", args=[self.run.pk]),
            reverse("checklists:submit", args=[self.run.pk]), reverse("checklists:history"),
            reverse("checklists:issue", args=[self.issue.pk]),
            reverse("checklists:report_issue", args=[self.run.pk, self.issue.response_id]),
            reverse("timeclock:clock"), reverse("timeclock:mine") + "?from=2026-08-01&to=2030-01-01",
            reverse("timeclock:shift", args=[self.shift.pk]),
        ]
        if manager:
            common += [
                reverse("staff:list"), reverse("staff:list") + "?status=all&role=EMPLOYEE&q=a",
                reverse("staff:create"), reverse("staff:detail", args=[self.anna.pk]),
                reverse("staff:edit", args=[self.anna.pk]), reverse("staff:role", args=[self.anna.pk]),
                reverse("staff:deactivate", args=[self.anna.pk]),
                reverse("inventory:create"), reverse("inventory:edit", args=[self.item.pk]),
                reverse("inventory:history") + "?type=WASTE&from=2020-01-01&to=2030-01-01",
                reverse("checklists:templates"), reverse("checklists:template_create"),
                reverse("checklists:template_edit", args=[self.template.pk]), reverse("checklists:review"),
                reverse("checklists:issues"), reverse("checklists:issues") + "?status=all",
                reverse("timeclock:timesheets") + "?from=2026-08-01&to=2030-01-01",
                reverse("timeclock:correct", args=[self.shift.pk]),
            ]
        return common

    def test_every_page_renders_for_every_role(self):
        for user, manager in ((self.owner, True), (self.manager, True), (self.anna, False)):
            self.client.force_login(user)
            for url in self.urls(manager):
                resp = self.client.get(url, follow=True)
                self.assertEqual(resp.status_code, 200, f"{user.login_identifier} {url}")

    def test_login_page_renders(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
