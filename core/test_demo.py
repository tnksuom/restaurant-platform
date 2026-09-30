"""Demo mode: resets itself on start and shows demo accounts. Must never touch a real installation."""
import io

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.images import ImageStore
from core.testing import make_staff
from inventory.models import InventoryItem
from menu.models import MenuItem
from staff.models import Employee


class DemoSafetyTests(TestCase):
    def test_reset_refuses_outside_demo_mode(self):
        make_staff()
        with self.assertRaises(CommandError):
            call_command("demo_reset", stdout=io.StringIO())
        self.assertEqual(Employee.objects.count(), 4)

    @override_settings(DEBUG=False, DEMO_MODE=False)
    def test_seed_refuses_on_production(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", password="x", stdout=io.StringIO())

    def test_no_demo_hints_on_a_real_installation(self):
        make_staff()
        for url in (reverse("login"), reverse("website:home")):
            resp = self.client.get(url)
            self.assertNotContains(resp, "Demo accounts")
            self.assertNotContains(resp, "demo-banner")
            self.assertNotContains(resp, settings.DEMO_PASSWORD)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="demo-test-pass")
class DemoResetTests(TransactionTestCase):
    def tearDown(self):
        for root in (settings.PRIVATE_UPLOAD_ROOT, settings.PUBLIC_UPLOAD_ROOT):
            for p in ImageStore(root).root().iterdir():
                if p.is_file():
                    p.unlink()

    def test_reset_wipes_changes_and_reloads_example_restaurant(self):
        call_command("demo_reset", stdout=io.StringIO())
        first_photos = sorted(p.name for p in ImageStore(settings.PUBLIC_UPLOAD_ROOT).root().iterdir())
        self.assertTrue(first_photos)
        # A visitor changes things...
        InventoryItem.objects.filter(name="Tofu").update(name="Changed by visitor")
        MenuItem.objects.all().update(price=0)
        out = io.StringIO()
        call_command("demo_reset", stdout=out)
        self.assertIn("Demo reset", out.getvalue())
        # ...and the next start brings the original demo back, without leftover files.
        self.assertTrue(InventoryItem.objects.filter(name="Tofu").exists())
        self.assertFalse(InventoryItem.objects.filter(name="Changed by visitor").exists())
        self.assertFalse(MenuItem.objects.filter(price=0).exists())
        photos = {p.name for p in ImageStore(settings.PUBLIC_UPLOAD_ROOT).root().iterdir()}
        self.assertFalse(photos & set(first_photos))
        self.assertEqual(len(photos), len(first_photos))
        self.assertTrue(Employee.objects.get(login_identifier="mia").check_password("demo-test-pass"))

    def test_demo_pages_show_banner_and_one_tap_sign_in(self):
        call_command("demo_reset", stdout=io.StringIO())
        resp = self.client.get(reverse("login"))
        self.assertContains(resp, "Demo accounts")
        self.assertContains(resp, "demo-test-pass")
        self.assertContains(resp, "Mia Virtanen")
        self.assertContains(self.client.get(reverse("website:home")), "Demo restaurant with example content")
        resp = self.client.post(reverse("login"), {"username": "mia", "password": "demo-test-pass"}, follow=True)
        self.assertContains(resp, "Hello, Mia Virtanen")
        self.assertContains(resp, "Anything you change is reset")
