import uuid

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Category, MovementType, WasteReason

QTY_ATTRS = {"inputmode": "decimal", "autocomplete": "off", "pattern": r"-?[0-9]+([.,][0-9]{1,3})?"}


class ItemForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=150)
    category = forms.ChoiceField(label=_("Category"), choices=Category.choices)
    unit = forms.CharField(label=_("Unit"), max_length=20, help_text=_("For example kg, L or pcs. Cannot change after stock is recorded."))
    opening_quantity = forms.CharField(label=_("Opening quantity"), initial="0", widget=forms.TextInput(attrs=QTY_ATTRS))
    minimum_quantity = forms.CharField(
        label=_("Minimum quantity"), initial="0", widget=forms.TextInput(attrs=QTY_ATTRS),
        help_text=_("The item shows as low stock at or below this amount."),
    )
    supplier_name = forms.CharField(label=_("Supplier (optional)"), max_length=150, required=False)

    def __init__(self, *args, editing=False, unit_locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["opening_quantity"]
        if unit_locked:
            self.fields["unit"].disabled = True
            self.fields["unit"].help_text = _("Locked because stock has been recorded for this item.")


class MovementForm(forms.Form):
    type = forms.ChoiceField(label=_("What happened?"), widget=forms.RadioSelect)
    quantity = forms.CharField(label=_("Quantity"), widget=forms.TextInput(attrs=QTY_ATTRS))
    waste_reason = forms.ChoiceField(
        label=_("Waste reason"), required=False, choices=[("", _("Choose a reason"))] + list(WasteReason.choices),
        help_text=_("Required for waste."),
    )
    note = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Optional, for example a delivery note number. Required for adjustments."),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, manager=False, **kwargs):
        super().__init__(*args, **kwargs)
        types = [MovementType.RECEIPT, MovementType.USAGE, MovementType.WASTE]
        if manager:
            types.append(MovementType.ADJUSTMENT)
            self.fields["quantity"].help_text = _("For an adjustment, use a minus sign to reduce stock, e.g. -1.5")
        self.fields["type"].choices = [(t.value, t.label) for t in types]
        if not self.is_bound:
            self.initial.setdefault("idempotency_key", uuid.uuid4())
            self.initial.setdefault("type", MovementType.RECEIPT)
