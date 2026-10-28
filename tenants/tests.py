"""The platform layer: isolation between organizations, locations, plans, status, modules,
sign-up and platform administration."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.testing import PASSWORD, make_employee, make_organization, make_restaurant
from inventory import services as inv
from inventory.models import InventoryItem
from staff import services as staff_services
from staff.models import Employee, Role
from website.models import GalleryImage

from . import services
from .middleware import SESSION_KEY
from .models import Organization, OrganizationEvent, Plan

ROOT = {"HTTP_HOST": "testserver"}  # the platform's own domain (no organization)


def host(org):
    return {"HTTP_HOST": f"{org.slug}.testserver"}


def org_with_staff(name, plan="business", **extra):
    org = make_organization(name, plan=plan, **extra)
    loc = make_restaurant(name, organization=org)
    owner = make_employee(loc, "owner", Role.OWNER)
    manager = make_employee(loc, "manager", Role.MANAGER)
    anna = make_employee(loc, "anna")
    return org, loc, owner, manager, anna


@override_settings(TENANT_DEFAULT_SLUG=None)
class IsolationTests(TestCase):
    def setUp(self):
        self.a, self.loc_a, self.owner_a, self.manager_a, self.anna_a = org_with_staff("Alpha Grill")
        self.b, self.loc_b, self.owner_b, self.manager_b, self.anna_b = org_with_staff("Beta Pho")

    def test_same_login_name_in_two_organizations_signs_in_to_the_right_one(self):
        self.anna_b.set_password("Beta-Password-2026")
        self.anna_b.save()
        resp = self.client.post("/login/", {"username": "ANNA", "password": PASSWORD}, follow=True, **host(self.a))
        self.assertContains(resp, "Hello, Anna")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.anna_a.pk)
        self.client.logout()
        # Beta's password does not open Alpha's anna, and vice versa.
        resp = self.client.post("/login/", {"username": "anna", "password": "Beta-Password-2026"}, **host(self.a))
        self.assertContains(resp, "did not work")
        resp = self.client.post("/login/", {"username": "anna", "password": "Beta-Password-2026"}, follow=True,
                                **host(self.b))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.anna_b.pk)

    def test_a_session_from_one_organization_is_signed_out_on_another(self):
        self.client.force_login(self.owner_a)
        self.assertEqual(self.client.get("/app/", **host(self.a)).status_code, 200)
        resp = self.client.get("/staff/", **host(self.b))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_and_records_of_another_organization_are_not_found(self):
        self.client.force_login(self.owner_a)
        page = self.client.get("/staff/", **host(self.a))
        self.assertContains(page, "Manager")
        self.assertEqual([e.pk for e in page.context["page"]], sorted(
            [self.owner_a.pk, self.manager_a.pk, self.anna_a.pk],
            key=lambda pk: Employee.objects.get(pk=pk).display_name))
        self.assertEqual(self.client.get(f"/staff/{self.anna_b.pk}/", **host(self.a)).status_code, 404)
        item = inv.create_item(self.manager_b, name="Rice", category="ingredient", unit="kg",
                               opening_quantity="5", minimum_quantity="1")
        self.assertEqual(self.client.get(f"/inventory/{item.pk}/", **host(self.a)).status_code, 404)

    def test_public_images_belong_to_their_organization(self):
        GalleryImage.objects.create(restaurant=self.loc_b, key="k" * 43 + ".jpg", width=10, height=10, alt="Room")
        self.assertEqual(self.client.get("/images/" + "k" * 43 + ".jpg", **host(self.a)).status_code, 404)

    def test_unknown_workspace_address(self):
        resp = self.client.get("/login/", HTTP_HOST="nobody.testserver")
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, "Workspace not found", status_code=404)

    def test_root_domain_has_no_staff_pages(self):
        self.assertEqual(self.client.get("/staff/", **ROOT).status_code, 404)
        self.assertContains(self.client.get("/", **ROOT), "Start free trial")


@override_settings(TENANT_DEFAULT_SLUG=None)
class LocationTests(TestCase):
    def setUp(self):
        self.org, self.loc1, self.owner, self.manager, self.anna = org_with_staff("Gamma Kitchen")

    def test_owner_adds_locations_up_to_the_plan_limit(self):
        self.org.plan = Plan.objects.get(code="pro")  # 3 locations
        self.org.save()
        services.add_location(self.owner, "Harbour")
        services.add_location(self.owner, "Station")
        with self.assertRaises(ValidationError) as ctx:
            services.add_location(self.owner, "Airport")
        self.assertIn("allows 3 location", ctx.exception.messages[0])
        with self.assertRaises(PermissionDenied):
            services.add_location(self.manager, "Anything")
        self.assertTrue(OrganizationEvent.objects.filter(action="location.added").exists())

    def test_switching_shows_only_that_locations_data(self):
        loc2 = services.add_location(self.owner, "Harbour")
        inv.create_item(self.manager, name="Rice", category="ingredient", unit="kg", opening_quantity="5",
                        minimum_quantity="1")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/inventory/", **host(self.org)), "Rice")
        self.client.post(reverse("tenants:switch_location"), {"location": loc2.pk, "next": "/inventory/"},
                         **host(self.org))
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.restaurant, loc2)
        self.assertNotContains(self.client.get("/inventory/", **host(self.org)), "Rice")

    def test_staff_can_only_switch_to_their_own_locations(self):
        loc2 = services.add_location(self.owner, "Harbour")
        with self.assertRaises(PermissionDenied):
            services.switch_location(self.anna, loc2.pk)
        self.client.force_login(self.anna)
        resp = self.client.post(reverse("tenants:switch_location"), {"location": loc2.pk}, **host(self.org))
        self.assertEqual(resp.status_code, 403)
        services.set_staff_locations(self.owner, self.anna, [self.loc1.pk, loc2.pk])
        self.assertEqual(services.switch_location(self.anna, loc2.pk), loc2)
        # The switcher in the top bar lists both.
        self.assertContains(self.client.get("/app/", **host(self.org)), 'id="location-select"')

    def test_only_owners_assign_locations_and_one_is_required(self):
        loc2 = services.add_location(self.owner, "Harbour")
        with self.assertRaises(PermissionDenied):
            services.set_staff_locations(self.manager, self.anna, [loc2.pk])
        with self.assertRaises(ValidationError):
            services.set_staff_locations(self.owner, self.anna, [])
        self.client.force_login(self.manager)
        resp = self.client.post(reverse("staff:locations", args=[self.anna.pk]), {"locations": [loc2.pk]},
                                **host(self.org))
        self.assertEqual(resp.status_code, 403)

    def test_managers_manage_staff_of_their_own_location_only(self):
        loc2 = services.add_location(self.owner, "Harbour")
        bao = make_employee(loc2, "bao")
        self.assertFalse(staff_services.can_manage(self.manager, bao))
        self.assertTrue(staff_services.can_manage(self.owner, bao))
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(f"/staff/{bao.pk}/", **host(self.org)).status_code, 404)
        self.assertNotContains(self.client.get("/staff/", **host(self.org)), "Bao")

    def test_removed_from_current_location_moves_to_an_allowed_one(self):
        loc2 = services.add_location(self.owner, "Harbour")
        services.set_staff_locations(self.owner, self.anna, [loc2.pk])  # no longer at loc1
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get("/app/", **host(self.org)).status_code, 200)
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.restaurant, loc2)


@override_settings(TENANT_DEFAULT_SLUG=None)
class PlanAndStatusTests(TestCase):
    def setUp(self):
        self.org, self.loc, self.owner, self.manager, self.anna = org_with_staff(
            "Delta Deli", plan="starter", status=Organization.Status.TRIAL, module_menu=False, module_website=False)

    def test_staff_limit_applies_to_new_and_reactivated_staff(self):
        small = Plan.objects.create(code="tiny", name="Tiny", monthly_price=Decimal("1"), max_locations=1, max_staff=4,
                                    is_public=False)
        self.org.plan = small
        self.org.save()
        cara = staff_services.create_employee(self.owner, "Cara", "cara", Role.EMPLOYEE)  # 4th active
        with self.assertRaises(ValidationError) as ctx:
            staff_services.create_employee(self.owner, "Dan", "dan", Role.EMPLOYEE)
        self.assertIn("allows 4 active staff", ctx.exception.messages[0])
        staff_services.deactivate(self.owner, cara)
        staff_services.create_employee(self.owner, "Dan", "dan", Role.EMPLOYEE)
        with self.assertRaises(ValidationError):
            staff_services.reactivate(self.owner, cara)

    def test_owner_changes_plan_during_trial_only(self):
        pro = Plan.objects.get(code="pro")
        services.change_plan(self.owner, self.org, pro)
        self.org.refresh_from_db()
        self.assertEqual(self.org.plan, pro)
        with self.assertRaises(PermissionDenied):
            services.change_plan(self.manager, self.org, Plan.objects.get(code="starter"))
        self.org.status = Organization.Status.ACTIVE
        self.org.save()
        with self.assertRaises(PermissionDenied):
            services.change_plan(self.owner, self.org, Plan.objects.get(code="business"))

    def test_downgrade_is_refused_when_the_organization_has_outgrown_the_plan(self):
        services.change_plan(self.owner, self.org, Plan.objects.get(code="pro"))
        services.add_location(self.owner, "Second")
        with self.assertRaises(ValidationError):
            services.change_plan(self.owner, self.org, Plan.objects.get(code="starter"))

    def test_ended_trial_pauses_the_workspace_but_keeps_login_and_plan_page(self):
        self.org.trial_ends_on = timezone.now().date() - timedelta(days=1)
        self.org.save()
        self.client.force_login(self.owner)
        resp = self.client.get("/app/", **host(self.org))
        self.assertContains(resp, "This workspace is paused", status_code=403)
        self.assertEqual(self.client.get("/org/account/", **host(self.org)).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get("/login/", **host(self.org)).status_code, 200)

    def test_trial_banner_counts_down(self):
        self.client.force_login(self.anna)
        self.assertContains(self.client.get("/app/", **host(self.org)), "Free trial: 14 days left.")

    def test_starter_plan_cannot_switch_on_the_website(self):
        services.set_modules(self.owner, self.org, {"timeclock", "checklists", "inventory", "website", "menu"})
        self.org.refresh_from_db()
        self.assertFalse(self.org.module_enabled("website"))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/menu-admin/", **host(self.org)).status_code, 404)
        # The public home redirects to the staff app instead.
        self.assertRedirects(self.client.get("/", **host(self.org)), "/app/", fetch_redirect_response=False)

    def test_switched_off_module_disappears_from_menu_and_urls(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/app/", **host(self.org)), "/inventory/")
        resp = self.client.post("/org/account/", {"action": "modules", "timeclock": "on", "checklists": "on"},
                                follow=True, **host(self.org))
        self.assertContains(resp, "Modules saved.")
        self.assertNotContains(self.client.get("/app/", **host(self.org)), "/inventory/")
        self.assertEqual(self.client.get("/inventory/", **host(self.org)).status_code, 404)
        self.assertTrue(OrganizationEvent.objects.filter(action="organization.modules_changed").exists())

    def test_history_is_append_only(self):
        event = OrganizationEvent.objects.create(organization=self.org, action="x")
        with self.assertRaises(TypeError):
            event.save()
        with connection.cursor() as cur, self.assertRaises(Exception):
            cur.execute("UPDATE tenants_organizationevent SET action='y' WHERE id=%s", [event.pk])


@override_settings(TENANT_DEFAULT_SLUG=None)
class SignupTests(TestCase):
    def data(self, **overrides):
        d = {"name": "Epsilon Eats", "slug": "Epsilon-Eats", "location_name": "", "time_zone": "Europe/Helsinki",
             "plan": Plan.objects.get(code="starter").pk, "owner_name": "Eeva", "owner_login": "eeva@example.com",
             "contact_email": "eeva@example.com", "password": "Kitchen-Door-2026", "password2": "Kitchen-Door-2026"}
        d.update(overrides)
        return d

    @override_settings(TENANT_SUBDOMAINS=False)
    def test_signup_on_a_single_domain_creates_the_workspace_and_signs_in(self):
        resp = self.client.post("/signup/", self.data(), follow=True, **ROOT)
        self.assertContains(resp, "Hello, Eeva")
        org = Organization.objects.get(slug="epsilon-eats")
        self.assertEqual((org.status, org.plan.code, org.trial_ends_on),
                         ("TRIAL", "starter", timezone.now().date() + timedelta(days=14)))
        owner = Employee.objects.get(organization=org)
        self.assertEqual((owner.role, owner.restaurant.name), (Role.OWNER, "Epsilon Eats"))
        self.assertFalse(org.module_enabled("website"))
        self.assertEqual(self.client.session[SESSION_KEY], "epsilon-eats")

    def test_signup_with_subdomains_points_to_the_new_address(self):
        resp = self.client.post("/signup/", self.data(), **ROOT)
        self.assertContains(resp, "https://epsilon-eats.testserver/login/")
        resp = self.client.post("/login/", {"username": "eeva@example.com", "password": "Kitchen-Door-2026"},
                                follow=True, HTTP_HOST="epsilon-eats.testserver")
        self.assertContains(resp, "Hello, Eeva")

    def test_signup_validation(self):
        make_organization("Taken", slug="taken")
        for overrides, field, message in [
            ({"slug": "taken"}, "slug", "already taken"),
            ({"slug": "admin"}, "slug", "reserved"),
            ({"slug": "-bad-"}, "slug", "lowercase letters"),
            ({"password2": "Different-2026"}, "password2", "different"),
            ({"password": "password1", "password2": "password1"}, "password", "too common"),
        ]:
            resp = self.client.post("/signup/", self.data(**overrides), **ROOT)
            self.assertIn(message, " ".join(resp.context["form"].errors.get(field, [])), (overrides, field))
        hidden = Plan.objects.create(code="secret", name="Secret", monthly_price=0, max_locations=9, is_public=False)
        resp = self.client.post("/signup/", self.data(plan=hidden.pk), **ROOT)
        self.assertIn("plan", resp.context["form"].errors)
        self.assertEqual(Organization.objects.count(), 1)


@override_settings(TENANT_DEFAULT_SLUG=None, TENANT_SUBDOMAINS=False)
class SingleDomainWorkspaceTests(TestCase):
    """Hosts without wildcard sub-domains: the workspace is chosen once and kept in the session."""

    def setUp(self):
        self.org, self.loc, self.owner, self.manager, self.anna = org_with_staff("Zeta Bar")

    def test_find_workspace_then_sign_in_then_leave(self):
        self.assertEqual(self.client.get("/login/").status_code, 404)  # no workspace yet: platform pages
        resp = self.client.post("/find/", {"slug": "Zeta-Bar"})
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        login_page = self.client.get("/login/")
        self.assertContains(login_page, "Zeta Bar")
        self.assertContains(login_page, "Choose another workspace")
        resp = self.client.post("/login/", {"username": "anna", "password": PASSWORD}, follow=True)
        self.assertContains(resp, "Hello, Anna")
        self.client.post("/leave/")
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertContains(self.client.get("/"), "Start free trial")

    def test_unknown_workspace_code(self):
        resp = self.client.post("/find/", {"slug": "nope"})
        self.assertContains(resp, "No workspace has this address")

    def test_password_setup_link_names_the_workspace(self):
        cara = staff_services.create_employee(self.owner, "Cara", "cara", Role.EMPLOYEE)
        link = staff_services.issue_setup_link(self.owner, cara)
        self.assertTrue(link.endswith("?workspace=zeta-bar"))
        fresh = self.client_class()
        resp = fresh.get(link)
        self.assertEqual(resp.status_code, 200)
        fresh.post(link, {"new_password1": "Kitchen-Door-77", "new_password2": "Kitchen-Door-77"})
        resp = fresh.post("/login/", {"username": "cara", "password": "Kitchen-Door-77"}, follow=True)
        self.assertContains(resp, "Hello, Cara")


@override_settings(TENANT_DEFAULT_SLUG=None)
class PlatformAdminTests(TestCase):
    def setUp(self):
        self.admin = Employee.objects.create_platform_admin("root", "Root Admin", "Platform-Pass-2026")
        self.org, self.loc, self.owner, self.manager, self.anna = org_with_staff(
            "Eta Eatery", plan="starter", status=Organization.Status.TRIAL)

    def login_admin(self):
        resp = self.client.post("/platform/login/", {"username": "root", "password": "Platform-Pass-2026"}, **ROOT)
        self.assertRedirects(resp, "/platform/", fetch_redirect_response=False)

    def test_admin_signs_in_on_the_platform_domain_only(self):
        resp = self.client.post("/login/", {"username": "root", "password": "Platform-Pass-2026"}, **host(self.org))
        self.assertContains(resp, "did not work")
        resp = self.client.post("/platform/login/", {"username": "owner", "password": PASSWORD}, **ROOT)
        self.assertContains(resp, "did not work")  # restaurant staff are not platform admins
        self.login_admin()
        self.assertContains(self.client.get("/platform/", **ROOT), "Eta Eatery")
        # An admin session is not valid inside a customer's workspace.
        self.assertEqual(self.client.get("/app/", **host(self.org)).status_code, 302)

    def test_staff_cannot_open_platform_pages(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/platform/", **host(self.org)).status_code, 404)

    def test_suspend_requires_a_reason_and_pauses_the_workspace(self):
        self.login_admin()
        url = f"/platform/organizations/{self.org.pk}/"
        resp = self.client.post(url, {"action": "status", "status": "SUSPENDED", "note": ""}, **ROOT)
        self.assertContains(resp, "Give a reason")
        self.client.post(url, {"action": "status", "status": "SUSPENDED", "note": "Invoice 12 unpaid"}, **ROOT)
        self.org.refresh_from_db()
        self.assertEqual(self.org.status, "SUSPENDED")
        staff = self.client_class()
        staff.force_login(self.anna)
        resp = staff.get("/app/", **host(self.org))
        self.assertContains(resp, "Invoice 12 unpaid", status_code=403)
        self.client.post(url, {"action": "status", "status": "ACTIVE", "note": ""}, **ROOT)
        self.assertEqual(staff.get("/app/", **host(self.org)).status_code, 200)
        actions = list(self.org.events.order_by("id").values_list("action", flat=True))
        self.assertEqual(actions[-2:], ["organization.status_changed", "organization.status_changed"])

    def test_admin_extends_trial_and_changes_plan(self):
        self.login_admin()
        url = f"/platform/organizations/{self.org.pk}/"
        until = timezone.now().date() + timedelta(days=30)
        self.client.post(url, {"action": "trial", "until": until.isoformat()}, **ROOT)
        self.client.post(url, {"action": "plan", "plan": Plan.objects.get(code="business").pk}, **ROOT)
        self.org.refresh_from_db()
        self.assertEqual((self.org.trial_ends_on, self.org.plan.code), (until, "business"))

    def test_admin_creates_an_organization(self):
        self.login_admin()
        resp = self.client.post("/platform/organizations/new/", {
            "name": "Theta Café", "slug": "theta", "location_name": "Main", "time_zone": "Europe/Helsinki",
            "plan": Plan.objects.get(code="pro").pk, "owner_name": "Teemu", "owner_login": "teemu",
            "contact_email": "", "password": "Kitchen-Door-2026", "password2": "Kitchen-Door-2026",
        }, follow=True, **ROOT)
        self.assertContains(resp, "Theta Café was created.")
        org = Organization.objects.get(slug="theta")
        self.assertEqual(org.events.get().actor, self.admin)


class MigrationFromSingleRestaurantTests(TransactionTestCase):
    """A database from the single-restaurant app (restaurant-ops v1.0) moves into one organization."""

    def test_existing_restaurant_and_staff_join_a_default_organization(self):
        executor = MigrationExecutor(connection)
        before = [("core", "0003_append_only_triggers"), ("staff", "0001_initial"), ("tenants", "0002_plans_and_history")]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Restaurant = apps.get_model("core", "Restaurant")
        Employee_ = apps.get_model("staff", "Employee")
        r = Restaurant.objects.create(name="Old Bistro", time_zone="Europe/Helsinki")
        Employee_.objects.create(restaurant=r, login_identifier="olli", display_name="Olli", role="OWNER",
                                 password="!")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        org = Organization.objects.get(slug="default")
        self.assertEqual((org.name, org.status, org.plan.code), ("Old Bistro", "ACTIVE", "business"))
        olli = Employee.objects.get(login_identifier="olli")
        self.assertEqual(olli.organization, org)
        self.assertEqual(list(olli.locations.values_list("name", flat=True)), ["Old Bistro"])
