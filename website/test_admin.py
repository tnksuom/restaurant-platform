"""EP08: the owner keeps the public restaurant details current."""
import io
import json
import re
from datetime import date, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import AuditEvent
from core.testing import frozen, local, make_staff

from . import services
from .models import GalleryImage, OpeningHours, PublicProfile, SpecialDay

OWNER_PAGES = ["website:info", "website:hours", "website:special_day_create", "website:images"]


def png(name="photo.png", size=(900, 600), colour=(40, 120, 90)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def week(**days):
    """week(mon=[((11, 0), (14, 0))], ...) -> {0: [(time, time)], ...}"""
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return {i: [(time(*o), time(*c)) for o, c in days.get(n, [])] for i, n in enumerate(names)}


def info(**overrides):
    data = {"name": "Phở Corner", "tagline": "Noodles and more", "introduction": "Hello!\nWe cook.",
            "street_address": "Kauppakatu 12", "postal_code": "40100", "city": "Jyväskylä", "country": "Finland",
            "phone": "+358 40 123 4567", "email": "Hello@Example.com"}
    data.update(overrides)
    return data


class OwnerOnlyTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_pages_are_for_the_owner_only(self):
        for name in OWNER_PAGES:
            url = reverse(name)
            self.assertEqual(self.client.get(url).status_code, 302, name)  # anonymous -> sign-in
            for person in (self.manager, self.anna):
                self.client.force_login(person)
                self.assertEqual(self.client.get(url).status_code, 403, (name, person))
                self.assertEqual(self.client.post(url, {}).status_code, 403, (name, person))
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(url).status_code, 200, name)
            self.client.logout()

    def test_services_check_the_role_too(self):
        with self.assertRaises(PermissionDenied):
            services.save_restaurant_info(self.manager, **info())
        with self.assertRaises(PermissionDenied):
            services.save_weekly_hours(self.manager, week())
        with self.assertRaises(PermissionDenied):
            services.save_special_day(self.manager, None, date=date(2030, 1, 1), is_closed=True, note="", periods=[])
        with self.assertRaises(PermissionDenied):
            services.replace_site_image(self.manager, "logo", png())

    def test_menu_shows_owner_links_only_to_owner(self):
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse("today")), reverse("website:hours"))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("today"))
        for name in ("website:info", "website:hours", "website:images"):
            self.assertContains(page, reverse(name))


# US-08.01
class RestaurantInfoTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.client.force_login(self.owner)

    def test_owner_saves_details_and_they_appear_on_the_public_site(self):
        resp = self.client.post(reverse("website:info"), info(), follow=True)
        self.assertContains(resp, "Restaurant details saved")
        self.r.refresh_from_db()
        self.assertEqual(self.r.name, "Phở Corner")
        profile = PublicProfile.objects.get(restaurant=self.r)
        self.assertEqual(profile.email, "hello@example.com")
        self.client.logout()
        page = self.client.get(reverse("website:contact"))
        for text in ("Phở Corner", "Kauppakatu 12", "40100 Jyväskylä", "+358 40 123 4567", "hello@example.com"):
            self.assertContains(page, text)
        home = self.client.get(reverse("website:home"))
        self.assertContains(home, "Noodles and more")
        self.assertContains(home, "Hello!<br>We cook.")
        event = AuditEvent.objects.get(action="website.info_updated")
        self.assertEqual(event.changes["name"], {"before": "Test Bistro", "after": "Phở Corner"})

    def test_validation(self):
        cases = [
            ({"name": "  "}, "name", "Enter the restaurant name."),
            ({"phone": "call us"}, "phone", "Enter a phone number"),
            ({"email": "not-an-email"}, "email", "Enter a valid email address."),
            ({"city": ""}, "city", "Enter both the street address and the city"),
        ]
        for overrides, field, message in cases:
            with self.assertRaises(ValidationError) as ctx:
                services.save_restaurant_info(self.owner, **info(**overrides))
            self.assertIn(message, " ".join(ctx.exception.message_dict[field]))
        self.r.refresh_from_db()
        self.assertEqual(self.r.name, "Test Bistro")  # nothing saved
        resp = self.client.post(reverse("website:info"), info(phone="call us"))
        self.assertContains(resp, "Enter a phone number")

    def test_clearing_contact_details_is_allowed(self):
        services.save_restaurant_info(self.owner, **info())
        services.save_restaurant_info(self.owner, **info(street_address="", city="", phone="", email=""))
        page = self.client.get(reverse("website:contact"))
        self.assertContains(page, "Address coming soon.")
        self.assertContains(page, "Contact details coming soon.")


# US-08.02
class WeeklyHoursTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.client.force_login(self.owner)

    def post_hours(self, **fields):
        data = {}
        for key, value in fields.items():
            data[key] = value
        return self.client.post(reverse("website:hours"), data, follow=True)

    def test_hours_per_weekday_with_lunch_break_and_closed_days(self):
        resp = self.post_hours(d0_opens1="11:00", d0_closes1="14:00", d0_opens2="17:00", d0_closes2="22:00",
                               d4_opens1="11:00", d4_closes1="02:00")
        self.assertContains(resp, "Opening hours saved.")
        self.assertEqual(OpeningHours.objects.filter(restaurant=self.r).count(), 3)
        self.client.logout()
        page = self.client.get(reverse("website:contact")).content.decode()
        monday = re.search(r"Monday</th>\s*<td>(.*?)</td>", page).group(1)
        self.assertEqual(monday, "11:00–14:00<br>17:00–22:00")
        self.assertIn("Tuesday</th>\n        <td>Closed</td>", page)
        with frozen(local(2026, 9, 28, 15, 30)):  # Monday in the lunch break
            status = services.open_status(self.r)
        self.assertFalse(status["is_open"])
        self.assertEqual(status["next_opens_at"].hour, 17)
        with frozen(local(2026, 10, 3, 1, 0)):  # Saturday 01:00, Friday's late period
            self.assertTrue(services.open_status(self.r)["is_open"])

    def test_saving_again_replaces_the_hours_and_is_audited(self):
        services.save_weekly_hours(self.owner, week(mon=[((9, 0), (17, 0))]))
        services.save_weekly_hours(self.owner, week(tue=[((10, 0), (18, 0))]))
        self.assertEqual(list(OpeningHours.objects.values_list("weekday", flat=True)), [1])
        last = AuditEvent.objects.filter(action="website.hours_updated").order_by("id").last()
        self.assertEqual(last.changes["before"]["mon"], ["09:00-17:00"])
        self.assertEqual(last.changes["after"]["tue"], ["10:00-18:00"])

    def test_form_shows_the_current_hours(self):
        services.save_weekly_hours(self.owner, week(wed=[((11, 30), (20, 0))]))
        page = self.client.get(reverse("website:hours"))
        self.assertContains(page, 'name="d2_opens1" value="11:30"')
        self.assertContains(page, 'name="d2_closes1" value="20:00"')

    def test_invalid_hours_are_explained_and_nothing_changes(self):
        services.save_weekly_hours(self.owner, week(mon=[((9, 0), (17, 0))]))
        bad = [
            (week(mon=[((11, 0), (15, 0)), ((14, 0), (20, 0))]), "day_0", "overlap"),
            ({0: [(time(11, 0), None)]}, "day_0", "both the opening and the closing time"),
            (week(tue=[((12, 0), (12, 0))]), "day_1", "must differ"),
            (week(fri=[((18, 0), (3, 0))], sat=[((2, 0), (10, 0))]), "day_4", "runs past the opening time on Saturday"),
            (week(sun=[((20, 0), (1, 0))], mon=[((0, 30), (9, 0))]), "day_6", "Monday"),
        ]
        for data, field, message in bad:
            with self.assertRaises(ValidationError) as ctx:
                services.save_weekly_hours(self.owner, data)
            self.assertIn(message, " ".join(ctx.exception.message_dict[field]), data)
        self.assertEqual(list(OpeningHours.objects.values_list("weekday", "opens")), [(0, time(9, 0))])
        resp = self.post_hours(d0_opens1="11:00", d0_closes1="15:00", d0_opens2="14:00", d0_closes2="20:00")
        self.assertContains(resp, "Monday: the two opening periods overlap.")
        self.assertContains(resp, 'name="d0_opens2" value="14:00"')  # what was typed is kept


# US-08.03
class SpecialHoursTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.client.force_login(self.owner)
        # Mon–Fri 11–21, Sat 12–02
        services.save_weekly_hours(self.owner, week(**{d: [((11, 0), (21, 0))] for d in ("mon", "tue", "wed", "thu", "fri")},
                                                    sat=[((12, 0), (2, 0))]))

    def test_closed_holiday_overrides_regular_hours(self):
        with frozen(local(2026, 12, 1, 9)):
            self.client.force_login(self.owner)  # a session started in the real "now" has expired by December
            resp = self.client.post(reverse("website:special_day_create"),
                                    {"date": "2026-12-07", "is_closed": "on", "note": "Staff day"}, follow=True)
            self.assertContains(resp, "Special hours saved for 07.12.2026.")
            with frozen(local(2026, 12, 7, 12)):  # Monday noon, normally open
                status = services.open_status(self.r)
            self.assertFalse(status["is_open"])
            self.assertEqual(status["next_opens_at"].date(), date(2026, 12, 8))
            with frozen(local(2026, 12, 7, 12)):
                page = self.client.get(reverse("website:contact"))
            self.assertContains(page, "Special opening hours")
            self.assertContains(page, "Staff day")

    def test_special_hours_replace_the_regular_hours_on_that_date(self):
        with frozen(local(2026, 12, 20, 9)):
            services.save_special_day(self.owner, None, date=date(2026, 12, 24), is_closed=False, note="Christmas Eve",
                                      periods=[(time(10, 0), time(14, 0)), (None, None)])
        with frozen(local(2026, 12, 24, 13)):
            status = services.open_status(self.r)
        self.assertTrue(status["is_open"])
        self.assertEqual(status["closes_at"].time(), time(14, 0))
        with frozen(local(2026, 12, 24, 15)):
            self.assertFalse(services.open_status(self.r)["is_open"])  # regular hours would say open until 21

    def test_previous_nights_late_period_still_runs_into_a_special_day(self):
        with frozen(local(2026, 12, 1, 9)):
            services.save_special_day(self.owner, None, date=date(2026, 12, 6), is_closed=True, note="", periods=[])
        with frozen(local(2026, 12, 6, 1, 0)):  # Sunday 01:00, still Saturday night's period
            self.assertTrue(services.open_status(self.r)["is_open"])

    def test_validation(self):
        with frozen(local(2026, 12, 1, 9)):
            cases = [
                ({"date": date(2026, 11, 30), "is_closed": True}, "date", "today or a later date"),
                ({"date": date(2026, 12, 5), "is_closed": False, "periods": []}, "periods", "Closed all day"),
                ({"date": date(2026, 12, 5), "is_closed": False, "periods": [(time(10, 0), time(16, 0)),
                                                                              (time(15, 0), time(18, 0))]},
                 "periods", "overlap"),
            ]
            for kwargs, field, message in cases:
                kwargs = {"note": "", "periods": [], **kwargs}
                with self.assertRaises(ValidationError) as ctx:
                    services.save_special_day(self.owner, None, **kwargs)
                self.assertIn(message, " ".join(ctx.exception.message_dict[field]))
            services.save_special_day(self.owner, None, date=date(2026, 12, 5), is_closed=True, note="", periods=[])
            with self.assertRaises(ValidationError) as ctx:
                services.save_special_day(self.owner, None, date=date(2026, 12, 5), is_closed=True, note="", periods=[])
            self.assertIn("already has special hours", ctx.exception.message_dict["date"][0])

    def test_edit_and_remove(self):
        with frozen(local(2026, 12, 1, 9)):
            special = services.save_special_day(self.owner, None, date=date(2026, 12, 5), is_closed=True, note="",
                                                periods=[])
            self.client.force_login(self.owner)
            resp = self.client.post(reverse("website:special_day_edit", args=[special.pk]),
                                    {"date": "2026-12-05", "opens": "12:00", "closes": "16:00", "note": "Short day"},
                                    follow=True)
            self.assertContains(resp, "12:00–16:00")
            special.refresh_from_db()
            self.assertFalse(special.is_closed)
            self.client.post(reverse("website:special_day_delete", args=[special.pk]))
        self.assertFalse(SpecialDay.objects.exists())
        actions = list(AuditEvent.objects.filter(action__startswith="website.special_day").order_by("id")
                       .values_list("action", flat=True))
        self.assertEqual(actions, ["website.special_day_created", "website.special_day_updated", "website.special_day_deleted"])

    def test_search_engines_get_special_hours(self):
        with frozen(local(2026, 12, 1, 9)):
            services.save_special_day(self.owner, None, date=date(2026, 12, 6), is_closed=True, note="", periods=[])
            page = self.client.get(reverse("website:home")).content.decode()
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(data["specialOpeningHoursSpecification"],
                         [{"@type": "OpeningHoursSpecification", "validFrom": "2026-12-06", "validThrough": "2026-12-06",
                           "opens": "00:00", "closes": "00:00"}])

    def test_another_restaurants_special_day_is_not_reachable(self):
        from core.testing import make_employee, make_restaurant
        from staff.models import Role
        other = make_restaurant("Other")
        boss = make_employee(other, "boss", Role.OWNER)
        special = SpecialDay.objects.create(restaurant=other, date=date(2030, 1, 1), is_closed=True)
        self.assertEqual(self.client.get(reverse("website:special_day_edit", args=[special.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("website:special_day_delete", args=[special.pk])).status_code, 404)
        self.assertTrue(SpecialDay.objects.filter(pk=special.pk).exists())
        self.assertTrue(boss.pk)


# US-08.04
class SiteImageTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.client.force_login(self.owner)
        self.url = reverse("website:images")

    def profile(self):
        return PublicProfile.objects.get(restaurant=self.r)

    def test_upload_replace_and_remove_logo_and_homepage_photo(self):
        resp = self.client.post(self.url, {"action": "logo_upload", "logo-photo": png("logo.png", (2000, 2000))},
                                follow=True)
        self.assertContains(resp, "Logo saved.")
        logo = self.profile().logo_key
        self.assertEqual(Image.open(services.site_store().path(logo)).size, (512, 512))  # scaled down

        resp = self.client.post(self.url, {"action": "hero_upload", "hero-photo": png()}, follow=True)
        self.assertContains(resp, "Describe the photo")  # description is required for the big photo
        self.assertEqual(self.profile().hero_key, "")
        self.client.post(self.url, {"action": "hero_upload", "hero-photo": png(), "hero-alt": "Our dining room"})
        hero = self.profile().hero_key
        self.client.logout()
        home = self.client.get(reverse("website:home"))
        self.assertContains(home, f'src="/images/{hero}" alt="Our dining room"')
        self.assertContains(home, f"/images/{logo}")
        self.assertEqual(self.client.get(f"/images/{hero}").status_code, 200)

        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"action": "hero_upload", "hero-photo": png(colour=(1, 2, 3)), "hero-alt": "New"})
        new_hero = self.profile().hero_key
        self.assertNotEqual(new_hero, hero)
        self.assertFalse(services.site_store().path(hero).exists())  # old file deleted
        self.assertEqual(self.client.get(f"/images/{hero}").status_code, 404)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"action": "hero_remove"})
            self.client.post(self.url, {"action": "logo_remove"})
        self.assertEqual((self.profile().hero_key, self.profile().logo_key, self.profile().hero_alt), ("", "", ""))
        self.assertEqual(self.client.get(f"/images/{new_hero}").status_code, 404)
        self.assertNotContains(self.client.get(reverse("website:home")), "/images/")

    def test_rejected_file_changes_nothing(self):
        self.client.post(self.url, {"action": "logo_upload", "logo-photo": png()})
        logo = self.profile().logo_key
        fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        resp = self.client.post(self.url, {"action": "logo_upload", "logo-photo": fake})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "errorlist")
        self.assertEqual(self.profile().logo_key, logo)
        with self.assertRaises(ValidationError):
            fake.seek(0)
            services.replace_site_image(self.owner, "logo", fake)
        self.assertEqual(self.profile().logo_key, logo)

    @override_settings(MENU_PHOTO_MAX_BYTES=2_000)
    def test_too_large_file_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            services.replace_site_image(self.owner, "hero", png(size=(800, 800)), alt="Big")
        self.assertIn("too large", ctx.exception.message_dict["photo"][0])

    def test_gallery_add_describe_reorder_remove(self):
        for i, alt in enumerate(["Dining room", "Counter", "Terrace"]):
            self.client.post(self.url, {"action": "gallery_add", "gallery-photo": png(colour=(i * 50, 90, 90)),
                                        "gallery-alt": alt})
        images = list(GalleryImage.objects.filter(restaurant=self.r))
        self.assertEqual([g.alt for g in images], ["Dining room", "Counter", "Terrace"])
        self.client.post(self.url, {"action": "gallery_down", "image": images[0].pk})
        self.client.post(self.url, {"action": "gallery_alt", "image": images[2].pk, "alt": "Summer terrace"})
        self.assertEqual(list(GalleryImage.objects.values_list("alt", flat=True)), ["Counter", "Dining room", "Summer terrace"])
        self.client.logout()
        home = self.client.get(reverse("website:home")).content.decode()
        self.assertLess(home.index('alt="Counter"'), home.index('alt="Dining room"'))
        self.assertIn("Our restaurant", home)
        self.client.force_login(self.owner)
        key = images[1].key
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"action": "gallery_delete", "image": images[1].pk})
        self.assertEqual(GalleryImage.objects.count(), 2)
        self.assertEqual(self.client.get(f"/images/{key}").status_code, 404)
        self.assertFalse(services.site_store().path(key).exists())

    def test_gallery_needs_a_description_and_has_a_limit(self):
        with self.assertRaises(ValidationError):
            services.add_gallery_image(self.owner, png(), "  ")
        for i in range(GalleryImage.MAX_PER_RESTAURANT):
            services.add_gallery_image(self.owner, png(colour=(i, i, i)), f"Photo {i}")
        with self.assertRaises(ValidationError) as ctx:
            services.add_gallery_image(self.owner, png(), "One more")
        self.assertIn("at most 8 photos", ctx.exception.message_dict["photo"][0])
        self.assertContains(self.client.get(self.url), "The gallery is full.")

    def test_unknown_image_or_action_is_not_found(self):
        self.assertEqual(self.client.post(self.url, {"action": "gallery_delete", "image": 99999}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "explode"}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "gallery_up"}).status_code, 404)

    def test_audit_trail(self):
        services.replace_site_image(self.owner, "hero", png(), alt="Room")
        services.set_hero_alt(self.owner, "Room at night")
        image = services.add_gallery_image(self.owner, png(), "Bar")
        services.delete_gallery_image(self.owner, image.pk)
        self.assertEqual(
            list(AuditEvent.objects.filter(action__startswith="website.").order_by("id").values_list("action", flat=True)),
            ["website.hero_added", "website.hero_described", "website.gallery_added", "website.gallery_removed"],
        )
