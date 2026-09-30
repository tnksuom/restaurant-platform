from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.testing import make_staff

from . import services
from .models import ChecklistIssue, ChecklistRun, ResponseStatus, ResponseType

SHEET = (
    "Thiết bị & Vệ sinh\tKiểm tra tủ lạnh\t\t\t\n"
    "\tXà bông rửa chén\n"
    "\tKhăn lau\n"
    "Rau củ & Đồ tươi\tDưa leo\tĐủ\t2 kg\tLinh\t30.9\n"  # extra columns are ignored
    "\tCà chua\n"
    "\n"
)


class PasteParserTests(TestCase):
    def test_google_sheets_columns_with_merged_group_cells(self):
        self.assertEqual(services.parse_task_lines(SHEET), [
            ("Thiết bị & Vệ sinh", "Kiểm tra tủ lạnh"),
            ("Thiết bị & Vệ sinh", "Xà bông rửa chén"),
            ("Thiết bị & Vệ sinh", "Khăn lau"),
            ("Rau củ & Đồ tươi", "Dưa leo"),
            ("Rau củ & Đồ tươi", "Cà chua"),
        ])

    def test_typed_lines_with_group_headers(self):
        text = "Unlock door\n# Kitchen\nCheck gas\n  Wipe tables  \n#Bar\nIce machine"
        self.assertEqual(services.parse_task_lines(text), [
            ("", "Unlock door"), ("Kitchen", "Check gas"), ("Kitchen", "Wipe tables"), ("Bar", "Ice machine"),
        ])

    def test_nothing_useful(self):
        self.assertEqual(services.parse_task_lines("\n \n\t\t\n"), [])


class GroupedStockChecklistTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.client.force_login(self.manager)
        resp = self.client.post(reverse("checklists:template_create"), {
            "title": "Mở cửa", "kind": "OPENING", "paste_tasks": SHEET, "paste_response_type": "STOCK_LEVEL",
            "tasks-TOTAL_FORMS": "1", "tasks-INITIAL_FORMS": "0", "tasks-MIN_NUM_FORMS": "0", "tasks-MAX_NUM_FORMS": "1000",
            "tasks-0-text": "Mở khóa cửa", "tasks-0-response_type": "CONFIRMATION", "tasks-0-required": "on",
        }, follow=True)
        self.assertContains(resp, "5 task(s) added from the pasted list")
        self.template = resp.context["request"].user.restaurant.checklist_templates.get()

    def test_pasted_tasks_are_grouped_stock_checks_after_manual_ones(self):
        tasks = list(self.template.active_tasks())
        self.assertEqual([(t.section, t.text, t.response_type) for t in tasks], [
            ("", "Mở khóa cửa", "CONFIRMATION"),
            ("Thiết bị & Vệ sinh", "Kiểm tra tủ lạnh", "STOCK_LEVEL"),
            ("Thiết bị & Vệ sinh", "Xà bông rửa chén", "STOCK_LEVEL"),
            ("Thiết bị & Vệ sinh", "Khăn lau", "STOCK_LEVEL"),
            ("Rau củ & Đồ tươi", "Dưa leo", "STOCK_LEVEL"),
            ("Rau củ & Đồ tươi", "Cà chua", "STOCK_LEVEL"),
        ])
        self.assertTrue(all(t.required for t in tasks))

    def test_groups_are_snapshotted_into_runs(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        t = self.template.active_tasks().get(text="Dưa leo")
        services.save_template(self.manager, self.template, title="Mở cửa", kind="OPENING", tasks=[
            {"id": t.pk, "section": "Renamed group", "text": "Dưa leo", "response_type": "STOCK_LEVEL", "required": True}
        ] + [{"id": o.pk, "remove": True, "text": "x", "response_type": "STOCK_LEVEL"}
             for o in self.template.active_tasks().exclude(pk=t.pk)])
        self.assertEqual(run.tasks.get(text="Dưa leo").section, "Rau củ & Đồ tươi")

    def test_stock_answers_enough_low_and_low_needs_quantity(self):
        run, _ = services.start_run(self.anna, self.template.pk)
        r = run.responses.get(task__text="Dưa leo")
        services.save_response(self.anna, r.pk, ResponseStatus.PASS)
        with self.assertRaises(ValidationError) as ctx:
            services.save_response(self.anna, r.pk, ResponseStatus.FAIL, note=" ")
        self.assertIn("how much is left", str(ctx.exception))
        services.save_response(self.anna, r.pk, ResponseStatus.FAIL, note="còn 1 kg")
        issue = ChecklistIssue.objects.get(response=r)
        self.assertEqual(issue.description, "Low: Dưa leo. Left: còn 1 kg")
        self.assertEqual(r.task.response_type, ResponseType.STOCK_LEVEL)

    def test_one_tap_saves_and_moves_to_next_task_with_inline_feedback(self):
        self.client.force_login(self.anna)
        self.client.post(reverse("checklists:start"), {"template": self.template.pk})
        run = ChecklistRun.objects.get()
        ids = list(run.tasks.order_by("position", "id").values_list("response__pk", flat=True))
        url = reverse("checklists:save_response", args=[run.pk, ids[1]])
        resp = self.client.post(url, {"status": "PASS", "note": ""})
        self.assertRedirects(resp, reverse("checklists:run", args=[run.pk]) + f"#task-{ids[2]}", fetch_redirect_response=False)
        page = self.client.get(reverse("checklists:run", args=[run.pk]))
        self.assertContains(page, "Saved “Kiểm tra tủ lạnh”: Enough.")
        body = page.content.decode()
        # The confirmation is shown on the next task, where the page lands.
        self.assertLess(body.index(f'id="task-{ids[2]}"'), body.index("Saved “Kiểm tra tủ lạnh”"))
        self.assertContains(page, "Thiết bị &amp; Vệ sinh")
        self.assertContains(page, "1/3")  # group progress
        self.assertContains(page, ">Enough<")
        # Feedback is shown once only.
        self.assertNotContains(self.client.get(reverse("checklists:run", args=[run.pk])), "Saved “")
        # A rejected answer stays on the same task, shows why, and keeps what was typed.
        url = reverse("checklists:save_response", args=[run.pk, ids[4]])
        resp = self.client.post(url, {"status": "FAIL", "note": "  "})
        self.assertTrue(resp["Location"].endswith(f"#task-{ids[4]}"))
        page = self.client.get(reverse("checklists:run", args=[run.pk]))
        self.assertContains(page, "how much is left")
        # Last task jumps to the submit button.
        url = reverse("checklists:save_response", args=[run.pk, ids[-1]])
        self.assertTrue(self.client.post(url, {"status": "PASS"})["Location"].endswith("#finish"))
