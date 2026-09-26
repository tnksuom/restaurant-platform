import threading
import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.testing import make_staff

from . import services
from .models import InventoryItem, MovementType, StockMovement, WasteReason

D = Decimal


def make_item(manager, opening="5", minimum="5", name="Flour", unit="kg"):
    return services.create_item(
        manager, name=name, category="ingredient", unit=unit, opening_quantity=opening, minimum_quantity=minimum
    )


class LedgerTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_walkthrough_balance_and_low_stock(self):
        item = make_item(self.manager, "5.000", "5.000")
        self.assertTrue(item.is_low)  # equality counts as low
        services.post_movement(self.anna, item.pk, MovementType.RECEIPT, "2.000", note="Delivery 123")
        services.post_movement(self.anna, item.pk, MovementType.USAGE, "1.000")
        services.post_movement(self.anna, item.pk, MovementType.WASTE, "0.500", waste_reason=WasteReason.EXPIRED)
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, D("5.500"))
        self.assertFalse(item.is_low)
        self.assertEqual(services.ledger_balance(item), D("5.500"))
        count = item.movements.count()
        with self.assertRaises(services.InsufficientStock):
            services.post_movement(self.anna, item.pk, MovementType.USAGE, "6.000")
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, D("5.500"))
        self.assertEqual(item.movements.count(), count)

    def test_low_stock_boundary(self):
        item = make_item(self.manager, "3", "2")
        self.assertFalse(item.is_low)
        services.post_movement(self.anna, item.pk, MovementType.USAGE, "1")
        item.refresh_from_db()
        self.assertTrue(item.is_low)
        low = InventoryItem.objects.filter(pk=item.pk, current_quantity__lte=D("2")).exists()
        self.assertTrue(low)

    def test_waste_is_distinct_and_needs_reason(self):
        item = make_item(self.manager, "5", "0")
        with self.assertRaises(ValidationError):
            services.post_movement(self.anna, item.pk, MovementType.WASTE, "1")
        with self.assertRaises(ValidationError):
            services.post_movement(self.anna, item.pk, MovementType.WASTE, "1", waste_reason=WasteReason.OTHER)
        m, _ = services.post_movement(self.anna, item.pk, MovementType.WASTE, "1", waste_reason=WasteReason.DAMAGED)
        self.assertEqual((m.type, m.signed_quantity, m.actor_id), ("WASTE", D("-1.000"), self.anna.pk))

    def test_quantity_validation(self):
        item = make_item(self.manager, "0", "0")
        for bad in ["0", "-1", "abc", "1.2345", "NaN", "Infinity", ""]:
            with self.assertRaises(ValidationError, msg=bad):
                services.post_movement(self.anna, item.pk, MovementType.RECEIPT, bad)
        m, _ = services.post_movement(self.anna, item.pk, MovementType.RECEIPT, "0,125")
        self.assertEqual(m.signed_quantity, D("0.125"))
        self.assertEqual(item.movements.count(), 1)

    def test_only_managers_adjust_and_adjustment_needs_reason(self):
        item = make_item(self.manager, "5", "0")
        with self.assertRaises(PermissionDenied):
            services.post_movement(self.anna, item.pk, MovementType.ADJUSTMENT, "-1", note="count")
        with self.assertRaises(ValidationError):
            services.post_movement(self.manager, item.pk, MovementType.ADJUSTMENT, "-1")
        services.post_movement(self.manager, item.pk, MovementType.ADJUSTMENT, "-1.5", note="Stock count")
        with self.assertRaises(services.InsufficientStock):
            services.post_movement(self.manager, item.pk, MovementType.ADJUSTMENT, "-10", note="oops")
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, D("3.500"))

    def test_employee_cannot_create_or_edit_items(self):
        with self.assertRaises(PermissionDenied):
            make_item(self.anna)
        item = make_item(self.manager)
        with self.assertRaises(PermissionDenied):
            services.update_item(self.anna, item, name="X", category="other", unit="kg", minimum_quantity="1")

    def test_duplicate_idempotency_key_posts_once(self):
        item = make_item(self.manager, "0", "0")
        key = uuid.uuid4()
        m1, created1 = services.post_movement(self.anna, item.pk, MovementType.RECEIPT, "2", idempotency_key=key)
        m2, created2 = services.post_movement(self.anna, item.pk, MovementType.RECEIPT, "2", idempotency_key=key)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(m1.pk, m2.pk)
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, D("2"))

    def test_unit_locked_after_movements_and_metadata_edit_keeps_history(self):
        item = make_item(self.manager, "5", "1")
        with self.assertRaises(ValidationError):
            services.update_item(self.manager, item, name="Flour", category="ingredient", unit="g", minimum_quantity="1")
        services.update_item(self.manager, item, name="Wheat flour", category="ingredient", unit="kg", minimum_quantity="2")
        self.assertEqual(item.movements.get().signed_quantity, D("5"))

    def test_duplicate_item_name_in_category_rejected(self):
        make_item(self.manager, name="Milk")
        with self.assertRaises(ValidationError):
            make_item(self.manager, name="MILK")
        # same name in a different category is allowed
        services.create_item(self.manager, name="Milk", category="drink", unit="L", opening_quantity="0", minimum_quantity="0")

    def test_deactivated_item_rejects_movements_but_keeps_history(self):
        item = make_item(self.manager)
        services.set_item_active(self.manager, item, False)
        with self.assertRaises(ValidationError):
            services.post_movement(self.anna, item.pk, MovementType.RECEIPT, "1")
        self.assertEqual(item.movements.count(), 1)

    def test_movements_are_immutable(self):
        item = make_item(self.manager)
        m = item.movements.get()
        with self.assertRaises(TypeError):
            m.save()
        with self.assertRaises(TypeError):
            m.delete()
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM inventory_stockmovement")

    def test_cached_balance_matches_ledger(self):
        item = make_item(self.manager, "10", "1")
        services.post_movement(self.anna, item.pk, MovementType.USAGE, "3.333")
        services.post_movement(self.manager, item.pk, MovementType.ADJUSTMENT, "0.333", note="recount")
        self.assertEqual(services.verify_ledger(), [])
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, D("7.000"))


class InventoryViewTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()
        self.item = make_item(self.manager, "5", "5")

    def test_employee_records_via_form_and_double_submit_is_ignored(self):
        self.client.force_login(self.anna)
        url = reverse("inventory:detail", args=[self.item.pk])
        data = {"type": "RECEIPT", "quantity": "2", "note": "", "waste_reason": "", "idempotency_key": str(uuid.uuid4())}
        self.client.post(url, data)
        resp = self.client.post(url, data, follow=True)
        self.assertContains(resp, "already saved")
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, D("7"))
        self.assertEqual(self.item.movements.filter(type="RECEIPT").get().actor, self.anna)

    def test_employee_cannot_post_adjustment_via_form(self):
        self.client.force_login(self.anna)
        url = reverse("inventory:detail", args=[self.item.pk])
        self.client.post(url, {"type": "ADJUSTMENT", "quantity": "-5", "note": "x", "idempotency_key": str(uuid.uuid4())})
        self.assertEqual(self.item.movements.count(), 1)

    def test_insufficient_stock_error_is_shown(self):
        self.client.force_login(self.anna)
        url = reverse("inventory:detail", args=[self.item.pk])
        resp = self.client.post(url, {"type": "USAGE", "quantity": "6", "idempotency_key": str(uuid.uuid4())})
        self.assertContains(resp, "Not enough stock")
        self.assertEqual(self.item.movements.count(), 1)

    def test_list_shows_low_stock_with_unit(self):
        self.client.force_login(self.anna)
        resp = self.client.get(reverse("inventory:list") + "?low=1")
        self.assertContains(resp, "Flour")
        self.assertContains(resp, "5 kg")
        self.assertContains(resp, "1 item low on stock")


class ConcurrentUsageTests(TransactionTestCase):
    def test_parallel_usage_cannot_overspend(self):
        r, owner, manager, anna, ben = make_staff()
        item = make_item(manager, "1", "0")
        barrier = threading.Barrier(2)
        results = []

        def use(actor):
            try:
                barrier.wait()
                services.post_movement(actor, item.pk, MovementType.USAGE, "1")
                results.append("ok")
            except services.InsufficientStock:
                results.append("rejected")
            finally:
                connection.close()

        threads = [threading.Thread(target=use, args=(a,)) for a in (anna, ben)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["ok", "rejected"])
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, D("0"))
        self.assertEqual(StockMovement.objects.filter(type="USAGE").count(), 1)
