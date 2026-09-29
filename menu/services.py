"""Menu management rules (EP02). Only owners and managers change the menu."""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils.translation import gettext as _

from core import audit
from core.images import ImageStore, prepare_image
from core.permissions import is_manager

from .models import Allergen, DietLabel, MenuCategory, MenuItem

MAX_PRICE = Decimal("99999.99")


def public_store():
    return ImageStore(settings.PUBLIC_UPLOAD_ROOT)


def _require_manager(actor):
    if not is_manager(actor):
        raise PermissionDenied


def parse_price(value):
    """Euros with at most two decimals. Accepts '12,50', '12.50' and '12,50 €'."""
    text = str(value if value is not None else "").strip().replace("€", "").replace(" ", "").replace(",", ".")
    if not text:
        raise ValidationError({"price": _("Enter a price.")})
    try:
        price = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValidationError({"price": _("Enter the price as a number, for example 12.50")})
    if not price.is_finite() or price < 0:
        raise ValidationError({"price": _("The price cannot be negative.")})
    if price.as_tuple().exponent < -2:
        raise ValidationError({"price": _("Use at most two decimals (cents).")})
    if price > MAX_PRICE:
        raise ValidationError({"price": _("That price is too large.")})
    return price.quantize(Decimal("0.01"))


# ------------------------------------------------------------------ categories


def _category_name(name):
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": _("Enter a category name.")})
    return name


@transaction.atomic
def create_category(actor, name):
    _require_manager(actor)
    name = _category_name(name)
    if MenuCategory.objects.filter(restaurant=actor.restaurant, name__iexact=name).exists():
        raise ValidationError({"name": _("A category with this name already exists.")})
    last = MenuCategory.objects.filter(restaurant=actor.restaurant).aggregate(m=Max("position"))["m"]
    try:
        with transaction.atomic():
            category = MenuCategory.objects.create(
                restaurant=actor.restaurant, name=name, position=(last or 0) + 1
            )
    except IntegrityError:
        raise ValidationError({"name": _("A category with this name already exists.")})
    audit.record(actor, "menu.category_created", category, {"name": name})
    return category


@transaction.atomic
def rename_category(actor, category_id, name):
    _require_manager(actor)
    category = MenuCategory.objects.select_for_update().get(pk=category_id, restaurant=actor.restaurant)
    name = _category_name(name)
    if MenuCategory.objects.filter(restaurant=actor.restaurant, name__iexact=name).exclude(pk=category.pk).exists():
        raise ValidationError({"name": _("A category with this name already exists.")})
    old = category.name
    category.name = name
    category.save(update_fields=["name", "updated_at"])
    if old != name:
        audit.record(actor, "menu.category_renamed", category, {"before": old, "after": name})
    return category


@transaction.atomic
def delete_category(actor, category_id):
    _require_manager(actor)
    category = MenuCategory.objects.select_for_update().get(pk=category_id, restaurant=actor.restaurant)
    if category.items.exists():
        raise ValidationError(_("Move or delete the dishes in this category first."))
    audit.record(actor, "menu.category_deleted", category, {"name": category.name})
    category.delete()


def _move(queryset, obj, direction):
    """Swap `obj` with its neighbour in `queryset` (ordered by position, id); renumbers to keep it tidy."""
    rows = list(queryset.select_for_update().order_by("position", "id"))
    index = next(i for i, row in enumerate(rows) if row.pk == obj.pk)
    target = index + (-1 if direction == "up" else 1)
    if 0 <= target < len(rows):
        rows[index], rows[target] = rows[target], rows[index]
    for pos, row in enumerate(rows, start=1):
        if row.position != pos:
            row.position = pos
            row.save(update_fields=["position", "updated_at"])


@transaction.atomic
def move_category(actor, category_id, direction):
    _require_manager(actor)
    category = MenuCategory.objects.get(pk=category_id, restaurant=actor.restaurant)
    _move(MenuCategory.objects.filter(restaurant=actor.restaurant), category, direction)


# ------------------------------------------------------------------ items


def _snapshot(item):
    return {
        "name": item.name, "category": item.category.name, "description": item.description,
        "price": str(item.price), "allergens": list(item.allergens), "diet_labels": list(item.diet_labels),
        "status": item.status, "is_available": item.is_available, "when_unavailable": item.when_unavailable,
        "photo": bool(item.photo_key),
    }


def _apply_fields(actor, item, data):
    errors = {}
    name = (data.get("name") or "").strip()
    if not name:
        errors["name"] = _("Enter the dish name.")
    category = None
    try:
        category = MenuCategory.objects.get(pk=data.get("category"), restaurant=actor.restaurant)
    except (MenuCategory.DoesNotExist, ValueError, TypeError):
        errors["category"] = _("Choose a category.")
    try:
        price = parse_price(data.get("price"))
    except ValidationError as exc:
        errors.update(exc.message_dict)
        price = None
    allergens = [a for a in (data.get("allergens") or []) if a in Allergen.values]
    diet = [d for d in (data.get("diet_labels") or []) if d in DietLabel.values]
    status = data.get("status") or MenuItem.Status.DRAFT
    if status not in MenuItem.Status.values:
        errors["status"] = _("Choose draft or published.")
    when = data.get("when_unavailable") or MenuItem.WhenUnavailable.MARK
    if when not in MenuItem.WhenUnavailable.values:
        errors["when_unavailable"] = _("Choose what to show when the dish is unavailable.")
    if errors:
        raise ValidationError(errors)
    if category.pk != getattr(item, "category_id", None):
        last = MenuItem.objects.filter(category=category).aggregate(m=Max("position"))["m"]
        item.position = (last or 0) + 1
    item.category = category
    item.name = name
    item.description = (data.get("description") or "").strip()
    item.price = price
    item.allergens = sorted(set(allergens), key=Allergen.values.index)
    item.diet_labels = sorted(set(diet), key=DietLabel.values.index)
    item.status = status
    item.is_available = bool(data.get("is_available", True))
    item.when_unavailable = when


def _prepare_photo(photo):
    return prepare_image(
        photo, max_bytes=settings.MENU_PHOTO_MAX_BYTES, max_side=settings.MENU_PHOTO_MAX_SIDE, field="photo"
    )


def save_item(actor, item, data, photo=None, remove_photo=False):
    """Create (item=None) or update a dish. A new photo replaces the old one.

    The photo is validated before anything is saved, so a rejected photo leaves the dish unchanged.
    """
    _require_manager(actor)
    prepared = _prepare_photo(photo) if photo else None
    store = public_store()
    new_key = store.write(prepared.data, prepared.ext) if prepared else None
    old_key = None
    try:
        with transaction.atomic():
            creating = item is None
            if creating:
                item = MenuItem(restaurant=actor.restaurant)
                before = None
            else:
                item = MenuItem.objects.select_for_update().select_related("category").get(
                    pk=item.pk, restaurant=actor.restaurant
                )
                before = _snapshot(item)
            _apply_fields(actor, item, data)
            if new_key:
                old_key = item.photo_key
                item.photo_key, item.photo_width, item.photo_height = new_key, prepared.width, prepared.height
            elif remove_photo and item.photo_key:
                old_key = item.photo_key
                item.photo_key, item.photo_width, item.photo_height = "", None, None
            item.full_clean(exclude=["restaurant", "category"])
            item.save()
            after = _snapshot(item)
            if creating:
                audit.record(actor, "menu.item_created", item, after)
            elif before != after or new_key:
                audit.record(actor, "menu.item_updated", item, {"before": before, "after": after})
    except Exception:
        if new_key:
            store.delete(new_key)
        raise
    if old_key:
        transaction.on_commit(lambda: store.delete(old_key))
    return item


@transaction.atomic
def set_available(actor, item_id, available):
    _require_manager(actor)
    item = MenuItem.objects.select_for_update().get(pk=item_id, restaurant=actor.restaurant)
    if item.is_available != available:
        item.is_available = available
        item.save(update_fields=["is_available", "updated_at"])
        audit.record(actor, "menu.item_available" if available else "menu.item_unavailable", item)
    return item


@transaction.atomic
def set_status(actor, item_id, status):
    _require_manager(actor)
    if status not in MenuItem.Status.values:
        raise ValidationError(_("Choose draft or published."))
    item = MenuItem.objects.select_for_update().get(pk=item_id, restaurant=actor.restaurant)
    if item.status != status:
        item.status = status
        item.save(update_fields=["status", "updated_at"])
        audit.record(actor, "menu.item_published" if status == MenuItem.Status.PUBLISHED else "menu.item_unpublished", item)
    return item


@transaction.atomic
def move_item(actor, item_id, direction):
    _require_manager(actor)
    item = MenuItem.objects.get(pk=item_id, restaurant=actor.restaurant)
    _move(MenuItem.objects.filter(category=item.category), item, direction)


@transaction.atomic
def delete_item(actor, item_id):
    _require_manager(actor)
    item = MenuItem.objects.select_for_update().get(pk=item_id, restaurant=actor.restaurant)
    key = item.photo_key
    audit.record(actor, "menu.item_deleted", item, _snapshot(item))
    item.delete()
    if key:
        transaction.on_commit(lambda: public_store().delete(key))


# ------------------------------------------------------------------ public view


def public_menu(restaurant):
    """Categories in order, each with its visible dishes; empty categories are left out."""
    items = (
        MenuItem.objects.filter(restaurant=restaurant, status=MenuItem.Status.PUBLISHED)
        .exclude(is_available=False, when_unavailable=MenuItem.WhenUnavailable.HIDE)
        .select_related("category")
        .order_by("category__position", "category_id", "position", "id")
    )
    sections, current = [], None
    for item in items:
        if current is None or current["category"].pk != item.category_id:
            current = {"category": item.category, "items": []}
            sections.append(current)
        current["items"].append(item)
    return sections


def can_view_photo(user, item):
    return item.is_public or (user.is_authenticated and is_manager(user) and user.restaurant_id == item.restaurant_id)
