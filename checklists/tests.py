import io
import os
import threading
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.testing import frozen, local, make_staff

from . import photos, services
from .models import ChecklistIssue, ChecklistRun, ResponseStatus, ResponseType


def task(text, rtype=ResponseType.CONFIRMATION, required=True, **kw):
    return {"text": text, "response_type": rtype, "required": required, **kw}


def make_template(manager, tasks=None, title="Opening"):
    tasks = tasks or [
        task("Unlock back door"),
        task("Fridge 1", ResponseType.TEMPERATURE, equipment_or_location="Walk-in fridge",
             min_temperature=Decimal("0"), max_temperature=Decimal("5")),
        task("Floor clean", ResponseType.PASS_FAIL, required=False),
    ]
    return services.save_template(manager, None, title=title, kind="OPENING", tasks=tasks)


def responses(run):
    return {r.task.text: r for r in run.responses.select_related("task")}


def image_file(fmt="PNG", name="photo.png", size=(40, 30), exif=None, noise=False):
    buf = io.BytesIO()
    if noise:  # incompressible, so the file is really large
        img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        img = Image.new("RGB", size, (200, 30, 30))
    kwargs = {"exif": exif} if exif is not None else {}
    img.save(buf, format=fmt, **kwargs)
    content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[fmt]
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


class TemplateAndRunTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.template = make_template(self.manager)

    def test_template_needs_a_task_and_only_managers_edit(self):
        with self.assertRaises(ValidationError):
            services.save_template(self.manager, None, title="Empty", kind="OTHER", tasks=[task("  ")])
        with self.assertRaises(PermissionDenied):
            make_template(self.anna)

    def test_start_twice_returns_same_run(self):
        run1, created1 = services.start_run(self.anna, self.template.pk)
        run2, created2 = services.start_run(self.ben, self.template.pk)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(run1.pk, run2.pk)
        self.assertEqual(ChecklistRun.objects.count(), 1)

    def test_extra_labeled_run_is_manager_only(self):
        services.start_run(self.anna, self.template.pk)
        with self.assertRaises(PermissionDenied):
            services.start_run(self.anna, self.template.pk, "Evening")
        run, created = services.start_run(self.manager, self.template.pk, "Evening")
        self.assertTrue(created)
        self.assertEqual(ChecklistRun.objects.count(), 2)

    def test_business_date_is_local(self):
        # 23:30 UTC on 26 Sep is 02:30 on 27 Sep in Helsinki
        with frozen(local(2026, 9, 27, 2, 30)):
            run, _ = services.start_run(self.anna, self.template.pk)
        self.assertEqual(str(run.business_date), "2026-09-27")

    def test_required_pending_blocks_submission(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        rs = responses(run)
        services.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.PASS)
        with self.assertRaises(ValidationError):
            services.submit_run(self.anna, run.pk)
        services.save_response(self.anna, rs["Fridge 1"].pk, ResponseStatus.PASS, temperature="3.5")
        services.submit_run(self.anna, run.pk)  # optional "Floor clean" may stay pending
        run.refresh_from_db()
        self.assertTrue(run.is_submitted)
        self.assertEqual(run.submitted_by, self.anna)

    def test_submitted_run_is_read_only(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        rs = responses(run)
        services.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.PASS)
        services.save_response(self.anna, rs["Fridge 1"].pk, ResponseStatus.NOT_APPLICABLE, note="Fridge off for defrost")
        services.submit_run(self.anna, run.pk)
        for actor in (self.anna, self.manager):
            with self.assertRaises(ValidationError):
                services.save_response(actor, rs["Unlock back door"].pk, ResponseStatus.FAIL, note="changed")
        with self.assertRaises(ValidationError):
            services.submit_run(self.anna, run.pk)

    def test_template_edits_do_not_change_started_runs(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        tasks = list(self.template.active_tasks())
        services.save_template(
            self.manager, self.template, title="Opening v2", kind="OPENING",
            tasks=[
                {"id": tasks[0].pk, "text": "Unlock ALL doors", "response_type": "CONFIRMATION", "required": True},
                {"id": tasks[1].pk, "text": "Fridge 1", "response_type": "TEMPERATURE", "required": True,
                 "min_temperature": Decimal("1"), "max_temperature": Decimal("4")},
                {"id": tasks[2].pk, "remove": True, "text": "x", "response_type": "PASS_FAIL"},
            ],
        )
        run.refresh_from_db()
        snap = {t.text: t for t in run.tasks.all()}
        self.assertEqual(run.title, "Opening")
        self.assertEqual(run.template_version, 1)
        self.assertIn("Unlock back door", snap)
        self.assertIn("Floor clean", snap)
        self.assertEqual(snap["Fridge 1"].max_temperature, Decimal("5"))
        self.template.refresh_from_db()
        self.assertEqual(self.template.version, 2)
        self.assertEqual(self.template.active_tasks().count(), 2)

    def test_notes_required_for_fail_and_na_and_whitespace_is_not_a_note(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        rs = responses(run)
        for status in (ResponseStatus.FAIL, ResponseStatus.NOT_APPLICABLE):
            with self.assertRaises(ValidationError):
                services.save_response(self.anna, rs["Unlock back door"].pk, status, note="   ")
        r = services.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.PASS, note="Used spare key")
        self.assertEqual((r.note, r.answered_by), ("Used spare key", self.anna))

    def test_failed_task_opens_issue_that_survives_submission(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        rs = responses(run)
        services.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.FAIL, note="Lock broken")
        services.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.FAIL, note="Still broken")
        issues = ChecklistIssue.objects.filter(response=rs["Unlock back door"])
        self.assertEqual(issues.count(), 1)  # re-saving links the existing open issue
        self.assertEqual(issues.get().description, "Lock broken")
        services.save_response(self.anna, rs["Fridge 1"].pk, ResponseStatus.PASS, temperature="2")
        services.submit_run(self.anna, run.pk)
        self.assertEqual(issues.get().status, ChecklistIssue.Status.OPEN)

    def test_out_of_range_temperature_opens_issue_with_reading(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        r = services.save_response(self.anna, responses(run)["Fridge 1"].pk, ResponseStatus.PASS, temperature="8,5")
        self.assertTrue(r.temperature_out_of_range)
        self.assertEqual(r.temperature_c, Decimal("8.5"))
        issue = ChecklistIssue.objects.get(response=r)
        self.assertEqual(issue.source, "TEMPERATURE")
        self.assertIn("8.5", issue.description)
        self.assertIn("Walk-in fridge", issue.description)

    def test_no_thresholds_means_no_verdict(self):
        t = make_template(self.manager, [task("Freezer", ResponseType.TEMPERATURE)], title="Freezer check")
        run, _ = services.start_run(self.anna, t.pk)
        r = services.save_response(self.anna, run.responses.get().pk, ResponseStatus.PASS, temperature="-30")
        self.assertIsNone(r.temperature_out_of_range)
        self.assertFalse(ChecklistIssue.objects.exists())

    def test_temperature_reading_is_required_and_validated(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        pk = responses(run)["Fridge 1"].pk
        for bad in ["", "warm", "1000"]:
            with self.assertRaises(ValidationError, msg=bad):
                services.save_response(self.anna, pk, ResponseStatus.PASS, temperature=bad)
        with self.assertRaises(ValidationError):
            services.save_response(self.anna, pk, ResponseStatus.FAIL, note="x")  # not an option for temperature

    def test_resolve_requires_manager_and_note_and_keeps_report(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        r = responses(run)["Unlock back door"]
        issue = services.report_issue(self.anna, r.pk, "Door handle loose")
        with self.assertRaises(PermissionDenied):
            services.resolve_issue(self.anna, issue.pk, "done")
        with self.assertRaises(ValidationError):
            services.resolve_issue(self.manager, issue.pk, "  ")
        services.resolve_issue(self.manager, issue.pk, "Handle tightened")
        issue.refresh_from_db()
        self.assertEqual(
            (issue.status, issue.description, issue.resolved_by, issue.resolution_note),
            ("RESOLVED", "Door handle loose", self.manager, "Handle tightened"),
        )

    def test_deactivated_template_stops_new_runs_but_keeps_history(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        services.set_template_active(self.manager, self.template, False)
        run2, created = services.start_run(self.ben, self.template.pk)  # today's run can still be resumed
        self.assertFalse(created)
        with frozen(local(2030, 1, 2, 9)):
            with self.assertRaises(ValidationError):
                services.start_run(self.anna, self.template.pk)
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("checklists:history"))
        self.assertContains(resp, "Opening")


class ChecklistViewTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.template = make_template(self.manager)

    def test_review_distinguishes_not_started_and_empty_day(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("checklists:review"))
        self.assertContains(resp, "Not started")
        self.assertEqual(resp.context["totals"]["not_started"], 1)
        self.assertEqual(resp.context["totals"]["submitted"], 0)
        services.set_template_active(self.manager, self.template, False)
        resp = self.client.get(reverse("checklists:review"))
        self.assertEqual(resp.context["expected"], 0)
        self.assertContains(resp, "No checklists were expected")
        self.assertNotContains(resp, "100%")

    def test_employee_fills_run_via_http(self):
        self.client.force_login(self.anna)
        resp = self.client.post(reverse("checklists:start"), {"template": self.template.pk}, follow=True)
        run = ChecklistRun.objects.get()
        rs = responses(run)
        url = reverse("checklists:save_response", args=[run.pk, rs["Unlock back door"].pk])
        self.client.post(url, {"status": "PASS", "note": ""})
        url = reverse("checklists:save_response", args=[run.pk, rs["Fridge 1"].pk])
        resp = self.client.post(url, {"status": "PASS", "temperature": "9", "note": ""}, follow=True)
        self.assertContains(resp, "outside the configured range")
        resp = self.client.post(reverse("checklists:submit", args=[run.pk]), follow=True)
        self.assertContains(resp, "Checklist submitted")
        # employee can still see their submitted run, and it is read-only
        resp = self.client.get(reverse("checklists:run", args=[run.pk]))
        self.assertNotContains(resp, "Save answer")
        self.assertContains(resp, "9.00 °C")

    def test_employee_history_shows_only_own_submissions(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        rs = responses(run)
        services.save_response(self.anna, rs["Unlock back door"].pk, ResponseStatus.PASS)
        services.save_response(self.anna, rs["Fridge 1"].pk, ResponseStatus.PASS, temperature="3")
        services.submit_run(self.anna, run.pk)
        self.client.force_login(self.ben)
        resp = self.client.get(reverse("checklists:history"))
        self.assertEqual(len(resp.context["page"].object_list), 0)
        self.assertEqual(self.client.get(reverse("checklists:run", args=[run.pk])).status_code, 404)
        self.client.force_login(self.anna)
        resp = self.client.get(reverse("checklists:history"))
        self.assertEqual(len(resp.context["page"].object_list), 1)


@override_settings(ISSUE_PHOTO_MAX_BYTES=200_000)
class PhotoTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.template = make_template(self.manager)
        self.run, _ = services.start_run(self.anna, self.template.pk)
        self.response = responses(self.run)["Unlock back door"]

    def test_report_with_photo_and_permissions(self):
        self.client.force_login(self.anna)
        url = reverse("checklists:report_issue", args=[self.run.pk, self.response.pk])
        resp = self.client.post(url, {"description": "Broken lock", "photo": image_file()}, follow=True)
        self.assertContains(resp, "Issue reported with photo")
        issue = ChecklistIssue.objects.get()
        att = issue.attachments.get()
        self.assertEqual(att.content_type, "image/png")
        self.assertNotIn(att.original_filename, att.storage_key)
        self.assertGreaterEqual(len(att.storage_key), 40)
        photo_url = reverse("checklists:photo", args=[att.pk])
        self.client.force_login(self.manager)
        resp = self.client.get(photo_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        # another employee who took no part cannot read it (after submission)
        services.save_response(self.anna, responses(self.run)["Fridge 1"].pk, ResponseStatus.PASS, temperature="2")
        services.save_response(self.anna, self.response.pk, ResponseStatus.PASS)
        services.submit_run(self.anna, self.run.pk)
        self.client.force_login(self.ben)
        self.assertEqual(self.client.get(photo_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(photo_url).status_code, 302)

    def test_invalid_photo_saves_nothing(self):
        self.client.force_login(self.anna)
        url = reverse("checklists:report_issue", args=[self.run.pk, self.response.pk])
        fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png")
        resp = self.client.post(url, {"description": "Broken", "photo": fake})
        self.assertEqual(resp.status_code, 200)
        gif = image_file("GIF", "anim.gif")
        resp = self.client.post(url, {"description": "Broken", "photo": gif})
        self.assertContains(resp, "JPEG, PNG or WebP")
        big = image_file("PNG", "big.png", size=(400, 400), noise=True)
        with self.assertRaises(ValidationError):
            services.report_issue(self.anna, self.response.pk, "Broken", big)
        self.assertFalse(ChecklistIssue.objects.exists())
        self.assertEqual([p for p in photos.storage_root().glob("*")], [])

    def test_metadata_is_stripped(self):
        exif = Image.Exif()
        exif[0x010F] = "SecretCamera"  # Make
        upload = image_file("JPEG", "p.jpg", exif=exif.tobytes())
        issue = services.report_issue(self.anna, self.response.pk, "Leak", upload)
        att = issue.attachments.get()
        data = photos.path_for(att.storage_key).read_bytes()
        self.assertNotIn(b"SecretCamera", data)
        photos.delete_private(att.storage_key)

    def tearDown(self):
        for p in photos.storage_root().glob("*"):
            p.unlink()


class ConcurrentStartTests(TransactionTestCase):
    def test_simultaneous_start_creates_one_run(self):
        r, owner, manager, anna, ben = make_staff()
        template = make_template(manager)
        barrier = threading.Barrier(2)
        ids = []

        def start(actor):
            try:
                barrier.wait()
                run, _ = services.start_run(actor, template.pk)
                ids.append(run.pk)
            finally:
                connection.close()

        threads = [threading.Thread(target=start, args=(a,)) for a in (anna, ben)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(ChecklistRun.objects.count(), 1)
