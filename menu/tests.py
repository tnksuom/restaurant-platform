import io
import os
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import AuditEvent
from core.testing import make_staff

from . import services
from .models import MenuCategory, MenuItem


def jpeg(name="dish.jpg", size=(2400, 1800), noise=False):
    buf = io.BytesIO()
    if noise:
        img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        img = Image.new("RGB", size, (190, 120, 60))
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    img.save(buf, "JPEG", exif=exif.tobytes())
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def dish(manager, category, name="Phở bò", price="14.50", status="PUBLISHED", **extra):
    data = {"name": name, "category": category.pk, "price": price, "description": "Beef noodle soup",
            "status": status, "is_available": True, "when_unavailable": "MARK", **extra}
    return services.save_item(manager, None, data)


class MenuRulesTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.soups = services.create_category(self.manager, "Soups")

    def tearDown(self):
        for p in services.public_store().root().glob("*"):
            p.unlink()

    # US-02.02
    def test_required_fields_and_price_validation(self):
        base = {"name": "Bánh mì", "category": self.soups.pk, "price": "9.90", "status": "DRAFT"}
        for bad in ({"name": "  "}, {"category": ""}, {"price": ""}, {"price": "-1"}, {"price": "9.999"},
                    {"price": "abc"}, {"price": "100000"}):
            with self.assertRaises(ValidationError, msg=bad):
                services.save_item(self.manager, None, {**base, **bad})
        self.assertFalse(MenuItem.objects.exists())
        for text, expected in (("9,90", "9.90"), ("9.9 €", "9.90"), ("12", "12.00"), ("0", "0.00")):
            self.assertEqual(services.parse_price(text), Decimal(expected))

    def test_save_as_draft_or_published(self):
        draft = dish(self.manager, self.soups, name="Draft soup", status="DRAFT")
        live = dish(self.manager, self.soups, name="Live soup")
        self.assertFalse(draft.is_public)
        self.assertTrue(live.is_public)
        names = [i.name for s in services.public_menu(self.r) for i in s["items"]]
        self.assertEqual(names, ["Live soup"])

    def test_only_managers_change_the_menu(self):
        with self.assertRaises(PermissionDenied):
            services.create_category(self.anna, "Drinks")
        with self.assertRaises(PermissionDenied):
            dish(self.anna, self.soups)
        self.client.force_login(self.anna)
        for url in (reverse("menu:overview"), reverse("menu:item_create")):
            self.assertEqual(self.client.get(url).status_code, 403)

    # US-02.03
    def test_edit_updates_public_menu_and_is_audited(self):
        item = dish(self.manager, self.soups)
        mains = services.create_category(self.manager, "Mains")
        services.save_item(self.manager, item, {
            "name": "Phở gà", "category": mains.pk, "price": "13.90", "description": "Chicken noodle soup",
            "allergens": ["GLUTEN", "SOY", "NOT_A_THING"], "diet_labels": ["M"], "status": "PUBLISHED",
            "is_available": True, "when_unavailable": "MARK",
        })
        item.refresh_from_db()
        self.assertEqual((item.name, item.category, item.price), ("Phở gà", mains, Decimal("13.90")))
        self.assertEqual(item.allergens, ["GLUTEN", "SOY"])
        self.assertEqual(item.diet_labels, ["M"])
        self.client.logout()
        resp = self.client.get(reverse("website:menu"))
        self.assertContains(resp, "Phở gà")
        self.assertContains(resp, "€13.90")
        self.assertContains(resp, "Cereals containing gluten, Soy")
        self.assertTrue(AuditEvent.objects.filter(action="menu.item_updated", entity_id=str(item.pk)).exists())

    # US-02.04
    def test_unavailable_marked_or_hidden_and_can_be_enabled_again(self):
        marked = dish(self.manager, self.soups, name="Marked soup")
        hidden = dish(self.manager, self.soups, name="Hidden soup", when_unavailable="HIDE")
        services.set_available(self.manager, marked.pk, False)
        services.set_available(self.manager, hidden.pk, False)
        resp = self.client.get(reverse("website:menu"))
        self.assertContains(resp, "Marked soup")
        self.assertContains(resp, "Not available today")
        self.assertNotContains(resp, "Hidden soup")
        services.set_available(self.manager, hidden.pk, True)
        resp = self.client.get(reverse("website:menu"))
        self.assertContains(resp, "Hidden soup")

    # US-02.05
    def test_categories_add_rename_order_and_delete_only_when_empty(self):
        drinks = services.create_category(self.manager, "Drinks")
        with self.assertRaises(ValidationError):
            services.create_category(self.manager, "drinks")
        services.rename_category(self.manager, drinks.pk, "Beverages")
        dish(self.manager, self.soups, name="Soup")
        dish(self.manager, drinks, name="Iced coffee", price="4.50")
        self.assertEqual([s["category"].name for s in services.public_menu(self.r)], ["Soups", "Beverages"])
        services.move_category(self.manager, drinks.pk, "up")
        self.assertEqual([s["category"].name for s in services.public_menu(self.r)], ["Beverages", "Soups"])
        with self.assertRaises(ValidationError):
            services.delete_category(self.manager, drinks.pk)
        empty = services.create_category(self.manager, "Desserts")
        services.delete_category(self.manager, empty.pk)
        self.assertFalse(MenuCategory.objects.filter(pk=empty.pk).exists())

    def test_items_reorder_within_category(self):
        a = dish(self.manager, self.soups, name="A")
        dish(self.manager, self.soups, name="B")
        services.move_item(self.manager, a.pk, "down")
        self.assertEqual([i.name for i in services.public_menu(self.r)[0]["items"]], ["B", "A"])

    # US-02.06
    def test_photo_upload_replace_remove(self):
        item = services.save_item(self.manager, None, {
            "name": "Bún chả", "category": self.soups.pk, "price": "15", "status": "PUBLISHED",
            "is_available": True, "when_unavailable": "MARK"}, photo=jpeg())
        first = item.photo_key
        path = services.public_store().path(first)
        data = path.read_bytes()
        self.assertNotIn(b"SecretCamera", data)  # metadata stripped
        self.assertEqual((item.photo_width, item.photo_height), (1600, 1200))  # scaled down
        form = {"name": item.name, "category": self.soups.pk, "price": "15", "status": "PUBLISHED",
                "is_available": True, "when_unavailable": "MARK"}
        with self.captureOnCommitCallbacks(execute=True):
            item = services.save_item(self.manager, item, form, photo=jpeg("new.jpg", (800, 600)))
        self.assertNotEqual(item.photo_key, first)
        self.assertFalse(path.exists())  # old file removed after replace
        second = services.public_store().path(item.photo_key)
        with self.captureOnCommitCallbacks(execute=True):
            item = services.save_item(self.manager, item, form, remove_photo=True)
        self.assertEqual(item.photo_key, "")
        self.assertFalse(second.exists())

    @override_settings(MENU_PHOTO_MAX_BYTES=50_000)
    def test_invalid_photo_leaves_dish_unchanged(self):
        item = dish(self.manager, self.soups)
        form = {"name": "Renamed", "category": self.soups.pk, "price": "15", "status": "PUBLISHED",
                "is_available": True, "when_unavailable": "MARK"}
        fake = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        for bad in (fake, jpeg("big.jpg", (600, 600), noise=True)):
            with self.assertRaises(ValidationError):
                services.save_item(self.manager, item, form, photo=bad)
        item.refresh_from_db()
        self.assertEqual((item.name, item.photo_key), ("Phở bò", ""))
        self.assertEqual(list(services.public_store().root().glob("*")), [])

    def test_draft_dish_photo_is_not_public(self):
        item = services.save_item(self.manager, None, {
            "name": "Secret special", "category": self.soups.pk, "price": "20", "status": "DRAFT",
            "is_available": True, "when_unavailable": "MARK"}, photo=jpeg())
        url = reverse("website:image", args=[item.photo_key])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.manager)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Cache-Control"], "private, no-store")
        services.set_status(self.manager, item.pk, "PUBLISHED")
        self.client.logout()
        resp = self.client.get(url)
        self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(self.client.get(reverse("website:image", args=["nope.jpg"])).status_code, 404)


class MenuScreenTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.client.force_login(self.manager)

    def tearDown(self):
        for p in services.public_store().root().glob("*"):
            p.unlink()

    def test_manager_builds_menu_through_the_screens(self):
        self.client.post(reverse("menu:category_create"), {"name": "Starters"})
        cat = MenuCategory.objects.get()
        resp = self.client.post(reverse("menu:item_create"), {
            "name": "Gỏi cuốn", "category": cat.pk, "price": "8,50", "description": "Fresh spring rolls",
            "allergens": ["PEANUTS", "CRUSTACEANS"], "diet_labels": ["G"], "status": "PUBLISHED",
            "is_available": "on", "when_unavailable": "MARK", "photo": jpeg("rolls.jpg", (900, 600)),
        }, follow=True)
        self.assertContains(resp, "is saved and on the public menu")
        item = MenuItem.objects.get()
        self.assertEqual((item.price, item.allergens), (Decimal("8.50"), ["CRUSTACEANS", "PEANUTS"]))
        self.assertTrue(item.photo_key)
        resp = self.client.get(reverse("menu:item_edit", args=[item.pk]))
        self.assertContains(resp, reverse("website:image", args=[item.photo_key]))  # preview of current photo
        resp = self.client.post(reverse("menu:item_create"), {"name": "", "category": cat.pk, "price": "-2", "status": "DRAFT", "when_unavailable": "MARK"})
        self.assertContains(resp, "Enter the dish name.")
        self.assertContains(resp, "cannot be negative")
        self.client.post(reverse("menu:item_availability", args=[item.pk]), {"available": "0"})
        item.refresh_from_db()
        self.assertFalse(item.is_available)
        self.assertEqual(self.client.get(reverse("menu:overview")).status_code, 200)
