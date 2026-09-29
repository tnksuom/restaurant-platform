from decimal import Decimal

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from core.models import Restaurant


class Allergen(models.TextChoices):
    """The 14 allergens that EU food law requires restaurants to declare."""

    GLUTEN = "GLUTEN", _("Cereals containing gluten")
    CRUSTACEANS = "CRUSTACEANS", _("Crustaceans")
    EGGS = "EGGS", _("Eggs")
    FISH = "FISH", _("Fish")
    PEANUTS = "PEANUTS", _("Peanuts")
    SOY = "SOY", _("Soy")
    MILK = "MILK", _("Milk")
    NUTS = "NUTS", _("Tree nuts")
    CELERY = "CELERY", _("Celery")
    MUSTARD = "MUSTARD", _("Mustard")
    SESAME = "SESAME", _("Sesame")
    SULPHITES = "SULPHITES", _("Sulphites")
    LUPIN = "LUPIN", _("Lupin")
    MOLLUSCS = "MOLLUSCS", _("Molluscs")


class DietLabel(models.TextChoices):
    """Short diet markers as used on Finnish menus."""

    VE = "VE", _("Vegan")
    V = "V", _("Vegetarian")
    G = "G", _("Gluten-free")
    M = "M", _("Milk-free")
    L = "L", _("Lactose-free")


class MenuCategory(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="menu_categories")
    name = models.CharField(_("name"), max_length=100)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint("restaurant", Lower("name"), name="menu_category_name_unique"),
            models.CheckConstraint(condition=~Q(name=""), name="menu_category_name_nonempty"),
        ]

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        PUBLISHED = "PUBLISHED", _("Published")

    class WhenUnavailable(models.TextChoices):
        MARK = "MARK", _("Show it marked as unavailable")
        HIDE = "HIDE", _("Hide it from the menu")

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="menu_items")
    category = models.ForeignKey(MenuCategory, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(_("name"), max_length=120)
    description = models.TextField(_("description"), blank=True, max_length=600)
    price = models.DecimalField(
        _("price (€)"), max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    allergens = ArrayField(models.CharField(max_length=16, choices=Allergen.choices), default=list, blank=True)
    diet_labels = ArrayField(models.CharField(max_length=4, choices=DietLabel.choices), default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    is_available = models.BooleanField(_("available now"), default=True)
    when_unavailable = models.CharField(
        _("when unavailable"), max_length=4, choices=WhenUnavailable.choices, default=WhenUnavailable.MARK
    )
    position = models.PositiveIntegerField(default=0)
    photo_key = models.CharField(max_length=80, blank=True)
    photo_width = models.PositiveIntegerField(null=True, blank=True)
    photo_height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["restaurant", "status"])]
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="menu_item_price_nonnegative"),
            models.CheckConstraint(condition=~Q(name=""), name="menu_item_name_nonempty"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    @property
    def is_public(self):
        """Visible on the public menu (possibly marked as unavailable)."""
        return self.is_published and (self.is_available or self.when_unavailable == self.WhenUnavailable.MARK)

    def get_allergens_display(self):
        labels = dict(Allergen.choices)
        return [labels[a] for a in self.allergens if a in labels]

    def get_diet_labels_display(self):
        labels = dict(DietLabel.choices)
        return [(code, labels[code]) for code in DietLabel.values if code in self.diet_labels]
