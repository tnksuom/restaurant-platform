from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from core.models import AppendOnlyModel, Restaurant

QTY = {"max_digits": 15, "decimal_places": 3}


class Category(models.TextChoices):
    INGREDIENT = "ingredient", _("Ingredient")
    DRINK = "drink", _("Drink")
    PACKAGING = "packaging", _("Packaging")
    CLEANING_SUPPLY = "cleaning_supply", _("Cleaning supply")
    OTHER = "other", _("Other")


class InventoryItem(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="inventory_items")
    name = models.CharField(_("name"), max_length=150)
    category = models.CharField(_("category"), max_length=32, choices=Category.choices)
    unit = models.CharField(_("unit"), max_length=20, help_text=_("For example kg, L or pcs. Fixed once stock is recorded."))
    minimum_quantity = models.DecimalField(
        _("minimum quantity"), **QTY, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))]
    )
    supplier_name = models.CharField(_("supplier"), max_length=150, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    # Cached SUM(signed_quantity) of this item's movements, updated in the same
    # transaction as each movement under a row lock. `manage.py verify_stock_ledger`
    # checks it against the ledger.
    current_quantity = models.DecimalField(**QTY, default=Decimal("0"), editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint("restaurant", "category", Lower("name"), name="item_name_unique_in_category"),
            models.CheckConstraint(condition=Q(minimum_quantity__gte=0), name="item_minimum_nonnegative"),
            models.CheckConstraint(condition=Q(current_quantity__gte=0), name="item_balance_nonnegative"),
            models.CheckConstraint(condition=~Q(name=""), name="item_name_nonempty"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_low(self):
        return self.current_quantity <= self.minimum_quantity


class MovementType(models.TextChoices):
    OPENING_BALANCE = "OPENING_BALANCE", _("Opening balance")
    RECEIPT = "RECEIPT", _("Received")
    USAGE = "USAGE", _("Used")
    WASTE = "WASTE", _("Waste")
    ADJUSTMENT = "ADJUSTMENT", _("Adjustment")


class WasteReason(models.TextChoices):
    EXPIRED = "EXPIRED", _("Expired")
    DAMAGED = "DAMAGED", _("Damaged")
    SPILLED = "SPILLED", _("Spilled")
    OTHER = "OTHER", _("Other")


class StockMovement(AppendOnlyModel):
    """Immutable ledger row. Reversal = a new, attributed ADJUSTMENT."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="stock_movements")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    type = models.CharField(max_length=20, choices=MovementType.choices)
    signed_quantity = models.DecimalField(**QTY)
    balance_after = models.DecimalField(**QTY)
    waste_reason = models.CharField(max_length=16, choices=WasteReason.choices, blank=True)
    note = models.TextField(blank=True)
    reference = models.CharField(max_length=100, blank=True)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements")
    idempotency_key = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [models.Index(fields=["item", "recorded_at"]), models.Index(fields=["restaurant", "recorded_at"])]
        constraints = [
            models.UniqueConstraint(fields=["restaurant", "idempotency_key"], name="movement_idempotency_unique"),
            models.CheckConstraint(condition=Q(balance_after__gte=0), name="movement_balance_nonnegative"),
            models.CheckConstraint(
                condition=(
                    Q(type__in=["OPENING_BALANCE", "RECEIPT"], signed_quantity__gt=0)
                    | Q(type__in=["USAGE", "WASTE"], signed_quantity__lt=0)
                    | (Q(type="ADJUSTMENT") & ~Q(signed_quantity=0))
                ),
                name="movement_sign_matches_type",
            ),
            models.CheckConstraint(
                condition=Q(type="WASTE", waste_reason__gt="") | (~Q(type="WASTE") & Q(waste_reason="")),
                name="movement_waste_reason",
            ),
            models.CheckConstraint(
                condition=~Q(type="ADJUSTMENT") | ~Q(note=""), name="movement_adjustment_has_reason"
            ),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.signed_quantity} {self.item.unit} {self.item}"
