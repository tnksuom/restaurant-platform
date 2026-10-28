from datetime import date, time
from urllib.parse import unquote

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import Restaurant
from core.testing import frozen, local, make_staff
from menu import services as menu_services

from . import services
from .models import OpeningHours, Promotion, PublicProfile


def set_profile(restaurant):
    return PublicProfile.objects.create(
        restaurant=restaurant, tagline="Vietnamese kitchen", introduction="Family recipes since 1998.",
        street_address="Kauppakatu 12", postal_code="40100", city="Jyväskylä", phone="+358 40 123 4567",
        email="hello@example.com",
    )


def hours(restaurant, spec):
    for weekday, opens, closes in spec:
        OpeningHours.objects.create(restaurant=restaurant, weekday=weekday, opens=time(*opens), closes=time(*closes))


class PublicPagesTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        set_profile(self.r)
        # Mon–Fri 11–21, Sat 12–02 (past midnight), Sun closed
        hours(self.r, [(d, (11, 0), (21, 0)) for d in range(5)] + [(5, (12, 0), (2, 0))])

    # US-01.01 / 01.05
    def test_home_is_public_and_shows_essentials(self):
        with frozen(local(2026, 9, 29, 12)):  # Tuesday noon
            resp = self.client.get(reverse("website:home"))
        self.assertEqual(resp.status_code, 200)
        for text in ("Test Bistro", "Vietnamese kitchen", "Family recipes since 1998.", "Open now", "until 21:00",
                     'name="viewport"', 'href="/menu/"', 'href="/contact/"', "Staff sign-in"):
            self.assertContains(resp, text)
        self.assertNotContains(resp, "Clock in")  # no staff tools on the public site

    def test_public_pages_need_no_login(self):
        for name in ("website:home", "website:menu", "website:contact"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("today")).status_code, 302)  # staff app still protected

    def test_structured_data_describes_the_restaurant(self):
        resp = self.client.get(reverse("website:home"))
        self.assertContains(resp, '"@type": "Restaurant"')
        self.assertContains(resp, '"postalCode": "40100"')
        self.assertContains(resp, '"dayOfWeek": "Saturday", "opens": "12:00", "closes": "02:00"')

    # US-01.02
    def test_contact_shows_address_phone_email_and_hours(self):
        with frozen(local(2026, 10, 4, 13)):  # Sunday
            resp = self.client.get(reverse("website:contact"))
        for text in ("Kauppakatu 12", "40100 Jyväskylä", "+358 40 123 4567", 'href="tel:+358401234567"',
                     "hello@example.com", "11:00–21:00", "12:00–02:00", "Closed now", "opens Monday at 11:00"):
            self.assertContains(resp, text)
        self.assertContains(resp, '<tr class="today" aria-current="date">\n        <th scope="row">Sunday</th>', html=False)

    # US-01.03
    def test_map_link_opens_the_address(self):
        url = services.map_url(PublicProfile.objects.get())
        self.assertTrue(url.startswith("https://www.google.com/maps/search/?api=1&query="))
        self.assertEqual(unquote(url.split("query=")[1]), "Kauppakatu 12, 40100 Jyväskylä, Finland")
        self.assertContains(self.client.get(reverse("website:contact")), url.replace("&", "&amp;"))

    def test_open_status_across_midnight_and_closed_days(self):
        cases = [
            (local(2026, 9, 29, 10, 59), False, None, (11, 0)),  # Tue before opening
            (local(2026, 9, 29, 11, 0), True, (21, 0), None),
            (local(2026, 9, 29, 21, 0), False, None, (11, 0)),  # closing minute: closed, opens Wed
            (local(2026, 10, 3, 23, 30), True, (2, 0), None),  # Sat late
            (local(2026, 10, 4, 1, 59), True, (2, 0), None),  # early Sunday still Saturday's period
            (local(2026, 10, 4, 2, 0), False, None, (11, 0)),  # closed Sunday, opens Monday
        ]
        for at, is_open, closes, next_open in cases:
            st = services.open_status(self.r, at)
            self.assertEqual(st["is_open"], is_open, at)
            if closes:
                self.assertEqual((st["closes_at"].hour, st["closes_at"].minute), closes, at)
            if next_open:
                self.assertEqual((st["next_opens_at"].hour, st["next_opens_at"].minute), next_open, at)
        self.assertEqual(services.open_status(self.r, local(2026, 10, 4, 3))["next_opens_at"].date(), date(2026, 10, 5))

    def test_no_hours_or_no_restaurant(self):
        OpeningHours.objects.all().delete()
        resp = self.client.get(reverse("website:home"))
        self.assertNotContains(resp, "Open now")
        self.assertNotContains(resp, "Closed now")


class PromotionTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        set_profile(self.r)

    def promo(self, title, starts, ends=None, published=True):
        return services.save_promotion(self.manager, None, title=title, body="", starts_on=starts, ends_on=ends,
                                       is_published=published)

    # US-01.04
    def test_only_active_promotions_appear_and_expired_disappear(self):
        self.promo("Lunch deal", date(2026, 9, 1), date(2026, 9, 30))
        self.promo("Autumn menu", date(2026, 10, 1))
        self.promo("Old news", date(2026, 8, 1), date(2026, 8, 31))
        self.promo("Hidden", date(2026, 9, 1), published=False)
        with frozen(local(2026, 9, 30, 20)):
            resp = self.client.get(reverse("website:home"))
        self.assertContains(resp, "Lunch deal")  # last day still shows
        for gone in ("Autumn menu", "Old news", "Hidden"):
            self.assertNotContains(resp, gone)
        with frozen(local(2026, 10, 1, 0, 5)):  # just after midnight local time
            resp = self.client.get(reverse("website:home"))
        self.assertNotContains(resp, "Lunch deal")
        self.assertContains(resp, "Autumn menu")

    def test_promotion_validation_and_permissions(self):
        with self.assertRaises(ValidationError):
            self.promo("Backwards", date(2026, 9, 10), date(2026, 9, 1))
        with self.assertRaises(ValidationError):
            self.promo("  ", date(2026, 9, 10))
        with self.assertRaises(PermissionDenied):
            services.save_promotion(self.anna, None, title="X", body="", starts_on=date(2026, 9, 1), ends_on=None,
                                    is_published=True)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("website:promotions")).status_code, 403)

    def test_manager_screens(self):
        self.client.force_login(self.manager)
        resp = self.client.post(reverse("website:promotion_create"), {
            "title": "Happy hour", "body": "Drinks -20 %", "starts_on": "2026-09-01", "ends_on": "2026-12-31",
            "is_published": "on"}, follow=True)
        self.assertContains(resp, "Promotion saved.")
        p = Promotion.objects.get()
        self.client.post(reverse("website:promotion_delete", args=[p.pk]))
        self.assertFalse(Promotion.objects.exists())


class EmptyInstallTests(TestCase):
    def test_without_an_organization_the_platform_landing_page_is_shown(self):
        self.assertEqual(Restaurant.objects.count(), 0)
        resp = self.client.get("/")
        self.assertContains(resp, "Start free trial")

    def test_menu_without_dishes(self):
        make_staff()
        self.assertContains(self.client.get(reverse("website:menu")), "The menu is being updated")
        self.assertEqual(menu_services.public_menu(Restaurant.objects.get()), [])
