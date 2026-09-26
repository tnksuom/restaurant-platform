"""Inventory ledger rules (EP04)."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from core import audit
from core.permissions import is_manager
from core.timeutils import now

from .models import InventoryItem, MovementType, StockMovement, WasteReason

MAX_QTY = Decimal("999999999999.999")


class InsufficientStock(ValidationError):
    pass


def parse_quantity(value, allow_zero=False, allow_negative=False, field="quantity"):
    """Decimal with at most 3 fractional digits. Accepts '1,5' as well as '1.5'."""
    if isinstance(value, Decimal):
        qty = value
    else:
        text = str(value if value is not None else "").strip().replace(",", ".")
        try:
            qty = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValidationError({field: _("Enter a number, for example 2.5")})
    if not qty.is_finite():
        raise ValidationError({field: _("Enter a number, for example 2.5")})
    if qty.as_tuple().exponent < -3:
        raise ValidationError({field: _("Use at most three decimal places.")})
    if abs(qty) > MAX_QTY:
        raise ValidationError({field: _("That quantity is too large.")})
    if qty < 0 and not allow_negative:
        raise ValidationError({field: _("Quantity cannot be negative.")})
    if qty == 0 and not allow_zero:
        raise ValidationError({field: _("Quantity must be more than zero.")})
    return qty.quantize(Decimal("0.001"))


def _require_manager(actor):
    if not is_manager(actor):
        raise PermissionDenied


@transaction.atomic
def create_item(actor, *, name, category, unit, opening_quantity, minimum_quantity, supplier_name=""):
    _require_manager(actor)
    item = InventoryItem(
        restaurant=actor.restaurant,
        name=(name or "").strip(),
        category=category,
        unit=(unit or "").strip(),
        minimum_quantity=parse_quantity(minimum_quantity, allow_zero=True, field="minimum_quantity"),
        supplier_name=(supplier_name or "").strip(),
    )
    opening = parse_quantity(opening_quantity, allow_zero=True, field="opening_quantity")
    _validate_item(item)
    item.save()
    audit.record(actor, "inventory.item_created", item, _item_snapshot(item))
    if opening > 0:
        _post(actor, item, MovementType.OPENING_BALANCE, opening, note=_("Opening balance"))
    return item


def _validate_item(item):
    errors = {}
    if not item.name:
        errors["name"] = _("Enter a name.")
    if not item.unit:
        errors["unit"] = _("Enter a unit, for example kg.")
    if item.name and (
        InventoryItem.objects.filter(
            restaurant=item.restaurant, category=item.category, name__iexact=item.name
        )
        .exclude(pk=item.pk)
        .exists()
    ):
        errors["name"] = _("An item with this name already exists in this category.")
    if errors:
        raise ValidationError(errors)
    item.full_clean(exclude=["restaurant"])


def _item_snapshot(item):
    return {
        "name": item.name,
        "category": item.category,
        "unit": item.unit,
        "minimum_quantity": str(item.minimum_quantity),
        "supplier_name": item.supplier_name,
        "is_active": item.is_active,
    }


@transaction.atomic
def update_item(actor, item, *, name, category, unit, minimum_quantity, supplier_name=""):
    _require_manager(actor)
    item = InventoryItem.objects.select_for_update().get(pk=item.pk, restaurant=actor.restaurant)
    before = _item_snapshot(item)
    unit = (unit or "").strip()
    if unit != item.unit and item.movements.exists():
        raise ValidationError({"unit": _("The unit cannot change after stock has been recorded. Create a new item instead.")})
    item.name = (name or "").strip()
    item.category = category
    item.unit = unit
    item.minimum_quantity = parse_quantity(minimum_quantity, allow_zero=True, field="minimum_quantity")
    item.supplier_name = (supplier_name or "").strip()
    _validate_item(item)
    item.save()
    after = _item_snapshot(item)
    if before != after:
        audit.record(actor, "inventory.item_updated", item, {"before": before, "after": after})
    return item


@transaction.atomic
def set_item_active(actor, item, active):
    _require_manager(actor)
    item = InventoryItem.objects.select_for_update().get(pk=item.pk, restaurant=actor.restaurant)
    if item.is_active != active:
        item.is_active = active
        item.save(update_fields=["is_active", "updated_at"])
        audit.record(actor, "inventory.item_activated" if active else "inventory.item_deactivated", item)
    return item


# Who may post which movement type.
EMPLOYEE_TYPES = {MovementType.RECEIPT, MovementType.USAGE, MovementType.WASTE}
MANAGER_TYPES = EMPLOYEE_TYPES | {MovementType.ADJUSTMENT}


def post_movement(actor, item_id, movement_type, quantity, *, note="", waste_reason="", reference="", idempotency_key=None):
    """Record one stock event. Returns (movement, created).

    - The item row is locked (SELECT ... FOR UPDATE) so concurrent postings are
      serialized and cannot overspend.
    - A repeated idempotency_key returns the original movement instead of posting twice.
    """
    if not actor.is_active:
        raise PermissionDenied
    allowed = MANAGER_TYPES if is_manager(actor) else EMPLOYEE_TYPES
    if movement_type not in allowed:
        raise PermissionDenied
    note = (note or "").strip()
    if movement_type == MovementType.ADJUSTMENT:
        delta = parse_quantity(quantity, allow_negative=True)
        if not note:
            raise ValidationError({"note": _("An adjustment needs a reason.")})
    else:
        delta = parse_quantity(quantity)
        if movement_type in (MovementType.USAGE, MovementType.WASTE):
            delta = -delta
    if movement_type == MovementType.WASTE:
        if waste_reason not in WasteReason.values:
            raise ValidationError({"waste_reason": _("Choose why the stock was wasted.")})
        if waste_reason == WasteReason.OTHER and not note:
            raise ValidationError({"note": _("Describe the reason when choosing Other.")})
    else:
        waste_reason = ""

    try:
        with transaction.atomic():
            try:
                item = InventoryItem.objects.select_for_update().get(pk=item_id, restaurant=actor.restaurant)
            except InventoryItem.DoesNotExist:
                raise ValidationError({"item": _("Choose an item.")})
            if idempotency_key:
                existing = StockMovement.objects.filter(
                    restaurant=actor.restaurant, idempotency_key=idempotency_key
                ).first()
                if existing:
                    _check_same_request(existing, actor, item, movement_type)
                    return existing, False
            if not item.is_active:
                raise ValidationError({"item": _("This item is deactivated.")})
            movement = _post(
                actor, item, movement_type, delta, note=note, waste_reason=waste_reason,
                reference=reference, idempotency_key=idempotency_key,
            )
            return movement, True
    except IntegrityError:
        # Only reachable if the same key raced on two different items.
        existing = StockMovement.objects.filter(restaurant=actor.restaurant, idempotency_key=idempotency_key).first()
        if existing:
            _check_same_request(existing, actor, None, movement_type)
            return existing, False
        raise


def _check_same_request(existing, actor, item, movement_type):
    if existing.actor_id != actor.pk or existing.type != movement_type or (item and existing.item_id != item.pk):
        raise ValidationError(_("This form was already used for a different entry. Reload the page and try again."))


def _post(actor, item, movement_type, delta, *, note="", waste_reason="", reference="", idempotency_key=None):
    """Caller must hold the item's row lock inside a transaction."""
    new_balance = item.current_quantity + delta
    if new_balance < 0:
        raise InsufficientStock(
            {
                "quantity": _("Not enough stock: %(have)s %(unit)s available.")
                % {"have": item.current_quantity.normalize(), "unit": item.unit}
            }
        )
    ts = now()
    movement = StockMovement.objects.create(
        restaurant=item.restaurant,
        item=item,
        type=movement_type,
        signed_quantity=delta,
        balance_after=new_balance,
        waste_reason=waste_reason,
        note=note,
        reference=(reference or "").strip()[:100],
        occurred_at=ts,
        actor=actor,
        idempotency_key=idempotency_key,
    )
    item.current_quantity = new_balance
    item.save(update_fields=["current_quantity", "updated_at"])
    return movement


def ledger_balance(item):
    return item.movements.aggregate(total=Sum("signed_quantity"))["total"] or Decimal("0")


def verify_ledger(restaurant=None):
    """Items whose cached balance differs from the ledger sum."""
    qs = InventoryItem.objects.all()
    if restaurant:
        qs = qs.filter(restaurant=restaurant)
    mismatches = []
    for item in qs.annotate(ledger=Sum("movements__signed_quantity")):
        ledger = item.ledger or Decimal("0")
        if ledger != item.current_quantity:
            mismatches.append((item, item.current_quantity, ledger))
    return mismatches
