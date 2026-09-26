import threading
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.testing import HEL, frozen, local, make_staff
from core.timeutils import hours_display, parse_local_datetime

from . import services
from .models import Shift, ShiftEvent, TimeCorrection


def work_shift(employee, start, end, breaks=()):
    with frozen(start):
        shift = services.clock_in(employee)
    for b_start, b_end in breaks:
        with frozen(b_start):
            services.start_break(employee)
        with frozen(b_end):
            services.end_break(employee)
    with frozen(end):
        services.clock_out(employee)
    shift.refresh_from_db()
    return shift


class ClockTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_duplicate_clock_in_prevented(self):
        services.clock_in(self.anna)
        with self.assertRaises(ValidationError):
            services.clock_in(self.anna)
        self.assertEqual(Shift.objects.filter(employee=self.anna, status="OPEN").count(), 1)

    def test_break_cycle_and_worked_duration(self):
        shift = work_shift(
            self.anna, local(2026, 9, 10, 9, 0), local(2026, 9, 10, 17, 30),
            breaks=[(local(2026, 9, 10, 12, 0), local(2026, 9, 10, 12, 30))],
        )
        self.assertEqual(services.break_seconds(shift), 30 * 60)
        self.assertEqual(services.worked_seconds(shift), 8 * 3600)
        self.assertEqual(list(shift.events.values_list("type", flat=True)),
                         ["CLOCK_IN", "BREAK_START", "BREAK_END", "CLOCK_OUT"])

    def test_repeated_break_requests_do_not_overlap(self):
        with frozen(local(2026, 9, 10, 9)):
            services.clock_in(self.anna)
        with frozen(local(2026, 9, 10, 12)):
            b1, created1 = services.start_break(self.anna)
        with frozen(local(2026, 9, 10, 12, 5)):
            b2, created2 = services.start_break(self.anna)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(b1.pk, b2.pk)
        with frozen(local(2026, 9, 10, 12, 30)):
            services.end_break(self.anna)
        with frozen(local(2026, 9, 10, 12, 31)), self.assertRaises(ValidationError):
            services.end_break(self.anna)
        shift = services.open_shift(self.anna)
        self.assertEqual(services.break_seconds(shift), 30 * 60)

    def test_active_break_blocks_clock_out(self):
        with frozen(local(2026, 9, 10, 9)):
            services.clock_in(self.anna)
        with frozen(local(2026, 9, 10, 12)):
            services.start_break(self.anna)
        with frozen(local(2026, 9, 10, 13)), self.assertRaises(ValidationError) as ctx:
            services.clock_out(self.anna)
        self.assertIn("End your break first", str(ctx.exception))
        self.assertTrue(services.open_shift(self.anna))

    def test_second_clock_out_does_not_mutate_closed_shift(self):
        shift = work_shift(self.anna, local(2026, 9, 10, 9), local(2026, 9, 10, 17))
        with frozen(local(2026, 9, 10, 18)), self.assertRaises(ValidationError):
            services.clock_out(self.anna)
        shift.refresh_from_db()
        self.assertEqual(shift.clock_out_at, local(2026, 9, 10, 17))

    def test_overnight_shift_is_one_shift_on_start_date(self):
        shift = work_shift(self.anna, local(2026, 9, 11, 20, 0), local(2026, 9, 12, 4, 0))
        self.assertEqual(services.worked_seconds(shift), 8 * 3600)
        on_11 = services.shifts_in_range(Shift.objects.all(), date(2026, 9, 11), date(2026, 9, 11), HEL)
        on_12 = services.shifts_in_range(Shift.objects.all(), date(2026, 9, 12), date(2026, 9, 12), HEL)
        self.assertEqual(list(on_11), [shift])
        self.assertEqual(list(on_12), [])

    def test_dst_end_elapsed_duration(self):
        # Clocks go back 04:00 -> 03:00 on 25 Oct 2026 in Helsinki: 22:00 -> 06:00 is 9 real hours.
        shift = work_shift(self.anna, local(2026, 10, 24, 22, 0), local(2026, 10, 25, 6, 0))
        self.assertEqual(services.worked_seconds(shift), 9 * 3600)

    def test_dst_start_elapsed_duration(self):
        # Clocks go forward 03:00 -> 04:00 on 29 Mar 2026: 22:00 -> 06:00 is 7 real hours.
        shift = work_shift(self.anna, local(2026, 3, 28, 22, 0), local(2026, 3, 29, 6, 0))
        self.assertEqual(services.worked_seconds(shift), 7 * 3600)

    def test_nonexistent_local_time_rejected(self):
        with self.assertRaises(ValidationError):
            parse_local_datetime("2026-03-29T03:30", HEL)

    def test_rounding_only_at_display(self):
        a = work_shift(self.anna, local(2026, 9, 1, 9, 0, 0), local(2026, 9, 1, 9, 20, 0))
        b = work_shift(self.anna, local(2026, 9, 2, 9, 0, 0), local(2026, 9, 2, 9, 20, 0))
        _rows, total, _ = services.summarize([a, b])
        self.assertEqual(total, 2400)
        self.assertEqual(str(hours_display(total)), "0.67")  # not 0.33 + 0.33 = 0.66

    def test_open_shift_is_provisional_and_excluded_from_total(self):
        work_shift(self.anna, local(2026, 9, 1, 9), local(2026, 9, 1, 13))
        with frozen(local(2026, 9, 1, 14)):
            services.clock_in(self.anna)
        with frozen(local(2026, 9, 1, 16)):
            rows, total, open_count = services.summarize(Shift.objects.filter(employee=self.anna).order_by("clock_in_at"))
        self.assertEqual((total, open_count), (4 * 3600, 1))
        self.assertEqual(rows[1]["provisional"], 2 * 3600)
        self.assertIsNone(rows[1]["worked"])

    def test_clock_events_are_immutable(self):
        services.clock_in(self.anna)
        ev = ShiftEvent.objects.get()
        with self.assertRaises(TypeError):
            ev.save()
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE timeclock_shiftevent SET at = now()")


class CorrectionTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.shift = work_shift(
            self.anna, local(2026, 9, 10, 9, 0), local(2026, 9, 10, 17, 0),
            breaks=[(local(2026, 9, 10, 12, 0), local(2026, 9, 10, 12, 30))],
        )

    def correct(self, actor=None, **kw):
        params = {
            "clock_in_at": local(2026, 9, 10, 8, 30), "clock_out_at": local(2026, 9, 10, 17, 0),
            "breaks": [(local(2026, 9, 10, 12, 0), local(2026, 9, 10, 12, 30))], "reason": "Forgot to clock in",
        }
        params.update(kw)
        return services.correct_shift(actor or self.manager, self.shift, **params)

    def test_correction_requires_reason_and_keeps_old_values(self):
        with self.assertRaises(ValidationError):
            self.correct(reason="  ")
        c = self.correct()
        self.shift.refresh_from_db()
        self.assertTrue(self.shift.is_corrected)
        self.assertEqual(services.worked_seconds(self.shift), 8 * 3600)
        self.assertEqual(c.before["clock_in_at"], "2026-09-10T06:00:00+00:00")
        self.assertEqual(c.after["clock_in_at"], "2026-09-10T05:30:00+00:00")
        with self.assertRaises(ValidationError):  # identical values are not a correction
            self.correct()
        self.assertEqual(c.changed_by, self.manager)
        # original clock events untouched
        self.assertEqual(self.shift.events.get(type="CLOCK_IN").at, local(2026, 9, 10, 9, 0))
        with self.assertRaises(TypeError):
            c.save()

    def test_employee_cannot_correct(self):
        with self.assertRaises(PermissionDenied):
            self.correct(actor=self.anna)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("timeclock:correct", args=[self.shift.pk])).status_code, 403)

    def test_invalid_corrections_rejected(self):
        cases = [
            {"clock_out_at": local(2026, 9, 10, 8, 0)},  # out before in
            {"breaks": [(local(2026, 9, 10, 7, 0), local(2026, 9, 10, 7, 30))]},  # outside shift
            {"breaks": [(local(2026, 9, 10, 12, 0), local(2026, 9, 10, 13, 0)),
                        (local(2026, 9, 10, 12, 30), local(2026, 9, 10, 13, 30))]},  # overlap
            {"breaks": [(local(2026, 9, 10, 12, 0), None)]},  # open break in closed shift
            {"clock_out_at": None},  # reopening a closed shift
        ]
        for kw in cases:
            with self.assertRaises(ValidationError, msg=kw):
                self.correct(**kw)
        self.assertFalse(TimeCorrection.objects.exists())

    def test_cannot_overlap_another_shift(self):
        work_shift(self.anna, local(2026, 9, 10, 18, 0), local(2026, 9, 10, 22, 0))
        with self.assertRaises(ValidationError):
            self.correct(clock_out_at=local(2026, 9, 10, 19, 0))
        # another employee's shift at the same time is fine
        work_shift(self.ben, local(2026, 9, 10, 8, 0), local(2026, 9, 10, 18, 0))
        self.correct()

    def test_manager_closes_forgotten_open_shift(self):
        with frozen(local(2026, 9, 11, 9)):
            open_shift = services.clock_in(self.ben)
        with frozen(local(2026, 9, 12, 10)):
            services.correct_shift(
                self.manager, open_shift, clock_in_at=local(2026, 9, 11, 9), clock_out_at=local(2026, 9, 11, 17),
                breaks=[], reason="Forgot to clock out",
            )
        open_shift.refresh_from_db()
        self.assertEqual(open_shift.status, "CLOSED")
        self.assertIsNone(services.open_shift(self.ben))

    def test_employee_sees_correction_but_not_other_shifts(self):
        self.correct()
        self.client.force_login(self.anna)
        resp = self.client.get(reverse("timeclock:shift", args=[self.shift.pk]))
        self.assertContains(resp, "Forgot to clock in")
        self.assertContains(resp, "Correction history")
        other = work_shift(self.ben, local(2026, 9, 11, 9), local(2026, 9, 11, 10))
        self.assertEqual(self.client.get(reverse("timeclock:shift", args=[other.pk])).status_code, 404)
        resp = self.client.get(reverse("timeclock:mine") + "?from=2026-09-01&to=2026-09-30")
        self.assertContains(resp, "Corrected")
        self.assertNotContains(resp, "Ben")

    def test_correction_form_round_trip(self):
        self.client.force_login(self.manager)
        url = reverse("timeclock:correct", args=[self.shift.pk])
        resp = self.client.get(url)
        self.assertContains(resp, 'value="2026-09-10T09:00:00"')
        data = {
            "clock_in": "2026-09-10T08:45:00", "clock_out": "2026-09-10T17:00:00", "break_rows": "2",
            "break-0-start": "2026-09-10T12:00:00", "break-0-end": "2026-09-10T12:30:00",
            "break-1-start": "", "break-1-end": "", "reason": "Started early",
        }
        resp = self.client.post(url, data, follow=True)
        self.assertContains(resp, "Shift corrected")
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.clock_in_at, local(2026, 9, 10, 8, 45))


class ClockViewTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_clock_actions_apply_only_to_signed_in_user(self):
        self.client.force_login(self.anna)
        self.client.post(reverse("timeclock:action"), {"action": "clock_in", "employee": self.ben.pk})
        self.assertIsNotNone(services.open_shift(self.anna))
        self.assertIsNone(services.open_shift(self.ben))
        resp = self.client.post(reverse("timeclock:action"), {"action": "clock_in"}, follow=True)
        self.assertContains(resp, "already clocked in")

    def test_timesheet_totals_for_manager(self):
        work_shift(self.anna, local(2026, 9, 1, 9), local(2026, 9, 1, 17))
        work_shift(self.ben, local(2026, 9, 1, 10), local(2026, 9, 1, 14))
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("timeclock:timesheets") + "?from=2026-09-01&to=2026-09-01")
        self.assertEqual(resp.context["total"], 12 * 3600)
        resp = self.client.get(reverse("timeclock:timesheets") + f"?employee={self.ben.pk}&from=2026-09-01&to=2026-09-01")
        self.assertEqual(resp.context["total"], 4 * 3600)
        self.assertContains(resp, "local date they started")


class ConcurrentClockInTests(TransactionTestCase):
    def test_parallel_clock_in_opens_one_shift(self):
        r, owner, manager, anna, ben = make_staff()
        barrier = threading.Barrier(2)
        results = []

        def go():
            try:
                barrier.wait()
                services.clock_in(anna)
                results.append("ok")
            except ValidationError:
                results.append("rejected")
            finally:
                connection.close()

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["ok", "rejected"])
        self.assertEqual(Shift.objects.filter(employee=anna).count(), 1)


class SubSecondCorrectionTests(TestCase):
    def test_unchanged_fields_keep_exact_instants(self):
        r, owner, manager, anna, ben = make_staff()
        base = local(2026, 9, 10, 9, 0, 0)
        shift = work_shift(
            anna, base, base + timedelta(seconds=1, microseconds=900_000),
            breaks=[(base + timedelta(microseconds=300_000), base + timedelta(microseconds=600_000))],
        )
        self.client.force_login(manager)
        url = reverse("timeclock:correct", args=[shift.pk])
        form = self.client.get(url).content.decode()
        import re

        values = dict(re.findall(r'name="([\w-]+)" value="([^"]*)"', form))
        data = {k: values.get(k, "") for k in ("clock_in", "clock_out", "break-0-start", "break-0-end")}
        data.update({"break_rows": "3", "reason": "Test", "clock_out": "2026-09-10T09:05:00"})
        resp = self.client.post(url, data, follow=True)
        self.assertContains(resp, "Shift corrected")
        shift.refresh_from_db()
        b = shift.breaks.get()
        self.assertEqual(b.start_at, base + timedelta(microseconds=300_000))
        self.assertEqual(shift.clock_in_at, base)
