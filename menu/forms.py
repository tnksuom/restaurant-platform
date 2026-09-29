from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Allergen, DietLabel, MenuItem


class CategoryForm(forms.Form):
    name = forms.CharField(label=_("Category name"), max_length=100)


class ItemForm(forms.Form):
    name = forms.CharField(label=_("Dish name"), max_length=120, error_messages={"required": _("Enter the dish name.")})
    category = forms.ChoiceField(label=_("Category"), error_messages={"required": _("Choose a category.")})
    description = forms.CharField(
        label=_("Description"), required=False, max_length=600, widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Shown on the public menu. Up to 600 characters."),
    )
    price = forms.CharField(
        label=_("Price (€)"), widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
        help_text=_("For example 14.50"), error_messages={"required": _("Enter a price.")},
    )
    allergens = forms.MultipleChoiceField(
        label=_("Allergens"), choices=Allergen.choices, required=False, widget=forms.CheckboxSelectMultiple,
        help_text=_("Tick every allergen the dish contains."),
    )
    diet_labels = forms.MultipleChoiceField(
        label=_("Diet labels"), choices=[(c, f"{c} – {l}") for c, l in DietLabel.choices], required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    status = forms.ChoiceField(
        label=_("Menu status"), choices=MenuItem.Status.choices, widget=forms.RadioSelect,
        help_text=_("Drafts are only visible here. Published dishes appear on the public menu."),
    )
    is_available = forms.BooleanField(label=_("Available now"), required=False, initial=True)
    when_unavailable = forms.ChoiceField(
        label=_("When it is unavailable"), choices=MenuItem.WhenUnavailable.choices, widget=forms.RadioSelect,
    )
    photo = forms.ImageField(
        label=_("Photo"), required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text=_("JPEG, PNG or WebP, up to 10 MB. Large photos are scaled down."),
    )
    remove_photo = forms.BooleanField(label=_("Remove the current photo"), required=False)

    def __init__(self, *args, categories=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [("", _("Choose a category"))] + [(str(c.pk), c.name) for c in categories]
        self.initial.setdefault("status", MenuItem.Status.DRAFT)
        self.initial.setdefault("when_unavailable", MenuItem.WhenUnavailable.MARK)

    def clean_price(self):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .services import parse_price

        try:
            return str(parse_price(self.cleaned_data["price"]))
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.message_dict["price"])
