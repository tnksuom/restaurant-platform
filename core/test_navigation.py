from django.test import TestCase

from core.testing import make_staff


def host(org):
    return {"HTTP_HOST": f"{org.slug}.testserver"}


class NavigationTests(TestCase):
    """The staff app's navigation: grouped by area, shown by role and module, sign-out always visible."""

    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.org = self.r.organization

    def page(self, user):
        self.client.force_login(user)
        return self.client.get("/app/", **host(self.org))

    def test_sign_out_button_sits_in_the_top_bar(self):
        html = self.page(self.anna).content.decode()
        topbar = html[html.index('<header class="topbar">'):html.index("</header>")]
        signout = topbar[topbar.index('<form class="signout"'):]
        self.assertIn('action="/logout/"', signout)
        self.assertIn("Sign out", signout)

    def test_sign_out_button_signs_out(self):
        self.page(self.anna)
        self.client.post("/logout/", **host(self.org))
        resp = self.client.get("/app/", **host(self.org))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_toggle_is_called_navigation_and_food_menu_is_named_as_such(self):
        resp = self.page(self.owner)
        self.assertContains(resp, '<span class="nav-label">Navigation</span>', html=False)
        self.assertContains(resp, ">Food menu<", html=False)
        self.assertNotContains(resp, ">Menu<", html=False)

    def test_employee_sees_only_their_own_groups(self):
        resp = self.page(self.anna)
        for group in ("Time tracking", "Checklists", "Inventory", "Account"):
            self.assertContains(resp, f'class="nav-group" id="nav-', html=False)
            self.assertContains(resp, group)
        for hidden in ("Team", "Food menu and website", "Business", "Timesheets", "Checklist templates"):
            self.assertNotContains(resp, hidden)
        self.assertContains(resp, "My submitted checklists")

    def test_manager_sees_management_groups_but_not_business(self):
        resp = self.page(self.manager)
        for group in ("Team", "Food menu and website", "Timesheets", "Checklist templates", "Stock history"):
            self.assertContains(resp, group)
        self.assertNotContains(resp, 'id="nav-business"')
        self.assertNotContains(resp, "Website images")  # owner only

    def test_owner_sees_business_group(self):
        resp = self.page(self.owner)
        self.assertContains(resp, 'id="nav-business"')
        self.assertContains(resp, "Plan and modules")
        self.assertContains(resp, "Website images")

    def test_switched_off_modules_hide_their_whole_group(self):
        self.org.module_inventory = False
        self.org.module_menu = False
        self.org.module_website = False
        self.org.save()
        resp = self.page(self.owner)
        self.assertNotContains(resp, 'id="nav-inventory"')
        self.assertNotContains(resp, 'id="nav-site"')
        self.assertContains(resp, 'id="nav-time"')
