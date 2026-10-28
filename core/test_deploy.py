"""Deployment-related tests: platform admin bootstrap, health check, production settings helpers."""
import io
import os
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from config.settings import database_from_url
from core.models import Restaurant
from staff.models import Employee


def run_ensure_admin(**env):
    out = io.StringIO()
    keys = ["INITIAL_ADMIN_LOGIN", "INITIAL_ADMIN_NAME", "INITIAL_ADMIN_PASSWORD"]
    clean = {k: v for k, v in os.environ.items() if k not in keys}
    with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
        call_command("ensure_platform_admin", stdout=out)
    return out.getvalue()


class EnsurePlatformAdminTests(TestCase):
    def test_creates_platform_admin_once(self):
        msg = run_ensure_admin(INITIAL_ADMIN_LOGIN="Boss@Example.com", INITIAL_ADMIN_NAME="Olli",
                               INITIAL_ADMIN_PASSWORD="Kitchen-Door-2026")
        admin = Employee.objects.get()
        self.assertEqual((admin.login_identifier, admin.is_platform_admin, admin.organization, admin.restaurant),
                         ("boss@example.com", True, None, None))
        self.assertTrue(admin.check_password("Kitchen-Door-2026"))
        self.assertNotIn("Kitchen-Door-2026", msg)
        self.assertIn("remove INITIAL_ADMIN_PASSWORD", msg)
        # Running again (every deploy) changes nothing, even with a different password set.
        again = run_ensure_admin(INITIAL_ADMIN_LOGIN="other", INITIAL_ADMIN_PASSWORD="Another-Pass-2026")
        self.assertEqual(Employee.objects.count(), 1)
        self.assertIn("already exists", again)
        self.assertFalse(Restaurant.objects.exists())

    def test_does_nothing_without_variables(self):
        msg = run_ensure_admin()
        self.assertFalse(Employee.objects.exists())
        self.assertIn("Set INITIAL_ADMIN_LOGIN", msg)

    def test_rejects_weak_password_without_printing_it(self):
        with self.assertRaises(CommandError) as ctx:
            run_ensure_admin(INITIAL_ADMIN_LOGIN="boss", INITIAL_ADMIN_PASSWORD="password1")
        self.assertNotIn("password1", str(ctx.exception))
        self.assertFalse(Employee.objects.exists())


class HealthCheckTests(TestCase):
    def test_health_check_is_public_and_plain(self):
        resp = self.client.get(reverse("healthz"))
        self.assertEqual((resp.status_code, resp.content), (200, b"ok"))

    def test_health_check_accepts_internal_host_header(self):
        self.assertEqual(self.client.get("/healthz", HTTP_HOST="10.1.2.3:10000").status_code, 200)
        self.assertEqual(self.client.get("/", HTTP_HOST="10.1.2.3:10000").status_code, 400)  # other pages still checked

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^healthz$"])
    def test_https_redirect_except_health_check(self):
        self.assertEqual(self.client.get(reverse("website:home")).status_code, 301)
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)


class DatabaseUrlTests(TestCase):
    def test_parses_platform_url_with_ssl_option(self):
        cfg = database_from_url("postgresql://app:p%40ss@db.internal:6543/restaurant?sslmode=require")
        self.assertEqual((cfg["USER"], cfg["PASSWORD"], cfg["HOST"], cfg["PORT"], cfg["NAME"]),
                         ("app", "p@ss", "db.internal", "6543", "restaurant"))
        self.assertEqual(cfg["OPTIONS"], {"sslmode": "require"})
        self.assertNotIn("OPTIONS", database_from_url("postgres://u:p@h/db"))
