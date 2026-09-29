from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from checklists import services as cs
from checklists.models import ResponseStatus
from checklists.tests import make_template, responses
from core.testing import frozen, local, make_staff
from inventory import services as inv
from inventory.models import MovementType
from timeclock import services as ts

from . import services

NOW = local(2026, 9, 10, 15, 0)


def item(manager, name, opening, minimum, unit="kg"):
    return inv.create_item(
        manager, name=name, category="ingredient", unit=unit, opening_quantity=opening, minimum_quantity=minimum
    )


class DashboardTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def board(self, day=None, at=NOW, user=None):
        with frozen(at):
            return services.build(user or self.manager, day)

    # US-07.05
    def test_staff_status_shows_clocked_in_people_and_breaks(self):
        with frozen(local(2026, 9, 10, 9)):
            ts.clock_in(self.anna)
            ts.clock_in(self.ben)
        with frozen(local(2026, 9, 10, 12)):
            ts.start_break(self.ben)
        with frozen(local(2026, 9, 10, 12, 30)):
            ts.end_break(self.ben)
        with frozen(local(2026, 9, 10, 14, 45)):
            ts.start_break(self.anna)
        b = self.board()
        rows = {row["employee"].login_identifier: row for row in b.staff_on_shift}
        self.assertEqual(set(rows), {"anna", "ben"})
        self.assertTrue(rows["anna"]["on_break"])
        self.assertFalse(rows["ben"]["on_break"])
        self.assertEqual(rows["ben"]["provisional"], 5 * 3600 + 30 * 60)  # 09:00–15:00 minus 30 min
        self.assertEqual(rows["anna"]["provisional"], 5 * 3600 + 45 * 60)  # running break not counted
        self.assertEqual(b.on_break_count, 1)
        with frozen(local(2026, 9, 10, 15, 5)):
            ts.end_break(self.anna)
        with frozen(local(2026, 9, 10, 15, 10)):
            ts.clock_out(self.anna)
        self.assertEqual([r["employee"] for r in self.board(at=local(2026, 9, 10, 15, 11)).staff_on_shift], [self.ben])

    # US-07.01 hours
    def test_hours_separate_closed_and_provisional(self):
        with frozen(local(2026, 9, 10, 7)):
            ts.clock_in(self.anna)
        with frozen(local(2026, 9, 10, 11)):
            ts.clock_out(self.anna)
        with frozen(local(2026, 9, 10, 13)):
            ts.clock_in(self.ben)
        # a shift from yesterday must not count toward today
        with frozen(local(2026, 9, 9, 10)):
            ts.clock_in(self.manager)
        with frozen(local(2026, 9, 9, 18)):
            ts.clock_out(self.manager)
        h = self.board().hours
        self.assertEqual((h["closed_seconds"], h["closed_shifts"]), (4 * 3600, 1))
        self.assertEqual((h["provisional_seconds"], h["open_shifts"]), (2 * 3600, 1))
        self.assertEqual(h["people"], 2)

    # US-07.02
    def test_checklist_status_lists_incomplete_tasks_and_not_started(self):
        opening = make_template(self.manager)
        make_template(self.manager, title="Closing")
        with frozen(local(2026, 9, 10, 9)):
            run, _ = cs.start_run(self.anna, opening.pk)
            rs = responses(run)
            cs.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.PASS)
        b = self.board()
        self.assertEqual([t.title for t in b.not_started], ["Closing"])
        self.assertEqual(b.checklists["expected"], 2)
        self.assertEqual(b.checklists["in_progress"], 1)
        self.assertEqual(b.checklists["pending_required"], 1)
        pending = [(r.task.text, r.task.required, r.run.started_by) for r in b.incomplete_tasks]
        self.assertEqual(pending, [("Fridge 1", True, self.anna), ("Floor clean", False, self.anna)])  # required first
        self.assertEqual(b.checklists["percent"], 33)

    def test_empty_day_is_not_reported_as_complete(self):
        b = self.board()
        self.assertEqual(b.checklists["expected"], 0)
        self.assertIsNone(b.checklists["percent"])
        self.client.force_login(self.manager)
        with frozen(NOW):
            resp = self.client.get(reverse("dashboard:home"))
        self.assertContains(resp, "No checklists were expected")
        self.assertNotContains(resp, "100%")

    # US-07.04 + flagged temperatures
    def test_issues_open_and_resolved_are_distinguished(self):
        template = make_template(self.manager)
        with frozen(local(2026, 9, 8, 9)):  # older issue stays visible while open
            run_old, _ = cs.start_run(self.anna, template.pk)
            old = cs.report_issue(self.anna, responses(run_old)["Unlock back door"].pk, "Old lock problem")
        with frozen(local(2026, 9, 10, 9)):
            run, _ = cs.start_run(self.anna, template.pk)
            cs.save_response(self.anna, responses(run)["Fridge 1"].pk, ResponseStatus.PASS, temperature="9.5")
            fixed = cs.report_issue(self.anna, responses(run)["Unlock back door"].pk, "Hinge squeaks")
        with frozen(local(2026, 9, 10, 11)):
            cs.resolve_issue(self.manager, fixed.pk, "Oiled")
        b = self.board()
        self.assertEqual([i.description for i in b.open_issues][0], "Old lock problem")
        self.assertIn("Hinge squeaks", [i.description for i in b.resolved_issues])
        self.assertNotIn(fixed.pk, [i.pk for i in b.open_issues])
        self.assertEqual(len(b.open_issues), 2)  # old report + temperature issue
        self.assertEqual([r.temperature_c for r in b.flagged_temperatures], [Decimal("9.50")])
        # a different day shows its own readings but still every open issue
        other = self.board(day=date(2026, 9, 9))
        self.assertEqual(other.flagged_temperatures, [])
        self.assertEqual(len(other.open_issues), 2)
        self.assertEqual(other.resolved_issues, [])
        self.assertTrue(old.pk in [i.pk for i in other.open_issues])

    # US-07.03
    def test_low_stock_at_or_below_minimum_most_urgent_first(self):
        item(self.manager, "Rice", "10", "10")  # equal counts as low
        item(self.manager, "Tofu", "0", "3")  # empty
        item(self.manager, "Herbs", "1", "2")
        item(self.manager, "Flour", "20", "5")  # fine
        gone = item(self.manager, "Old sauce", "0", "1")
        inv.set_item_active(self.manager, gone, False)  # deactivated items are not listed
        names = [i.name for i in self.board().low_stock]
        self.assertEqual(names, ["Tofu", "Herbs", "Rice"])
        self.client.force_login(self.manager)
        with frozen(NOW):
            resp = self.client.get(reverse("dashboard:home"))
        self.assertContains(resp, reverse("inventory:detail", args=[InventoryId.of("Tofu")]))
        self.assertContains(resp, "0 kg")

    # access
    def test_only_managers_and_owners(self):
        with self.assertRaises(PermissionDenied):
            self.board(user=self.anna)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("today")), reverse("dashboard:home"))
        for user in (self.manager, self.owner):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("dashboard:home") + "?date=2026-09-01").status_code, 200)
            self.assertContains(self.client.get(reverse("today")), reverse("dashboard:home"))

    def test_invalid_date_falls_back_to_today(self):
        self.client.force_login(self.manager)
        with frozen(NOW):
            resp = self.client.get(reverse("dashboard:home") + "?date=not-a-date")
        self.assertEqual(resp.context["board"].day, date(2026, 9, 10))
        self.assertContains(resp, "Today's operation")


class InventoryId:
    @staticmethod
    def of(name):
        from inventory.models import InventoryItem

        return InventoryItem.objects.get(name=name).pk
