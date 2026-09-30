from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ResponseType, TemplateKind


class TemplateForm(forms.Form):
    title = forms.CharField(label=_("Title"), max_length=150)
    kind = forms.ChoiceField(label=_("Type"), choices=TemplateKind.choices)
    paste_tasks = forms.CharField(
        label=_("Add many tasks at once"), required=False, widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_(
            "Paste from a spreadsheet: select the Group and Task columns in Google Sheets or Excel (without the "
            "header row), copy, and paste here. Or type one task per line, and start a group with “# Group name”."
        ),
    )
    paste_response_type = forms.ChoiceField(
        label=_("Answer type for pasted tasks"), choices=ResponseType.choices, initial=ResponseType.STOCK_LEVEL,
        help_text=_("You can change individual tasks afterwards."),
    )


class TaskForm(forms.Form):
    id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    position = forms.IntegerField(label=_("Order"), required=False, min_value=0)
    section = forms.CharField(label=_("Group"), max_length=100, required=False)
    text = forms.CharField(label=_("Task"), max_length=300, required=False)
    response_type = forms.ChoiceField(label=_("Answer type"), choices=ResponseType.choices, initial=ResponseType.CONFIRMATION)
    required = forms.BooleanField(label=_("Required"), required=False, initial=True)
    equipment_or_location = forms.CharField(label=_("Equipment or location"), max_length=150, required=False)
    min_temperature = forms.DecimalField(
        label=_("Lowest acceptable °C"), required=False, max_digits=6, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.1", "inputmode": "decimal"}),
    )
    max_temperature = forms.DecimalField(
        label=_("Highest acceptable °C"), required=False, max_digits=6, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.1", "inputmode": "decimal"}),
    )
    remove = forms.BooleanField(label=_("Remove task"), required=False)


TaskFormSet = forms.formset_factory(TaskForm, extra=3)


class IssueForm(forms.Form):
    description = forms.CharField(label=_("What is the problem?"), widget=forms.Textarea(attrs={"rows": 3}))
    photo = forms.ImageField(
        label=_("Photo (optional)"), required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"}),
        help_text=_("JPEG, PNG or WebP, up to 10 MB."),
    )

    def clean_description(self):
        value = self.cleaned_data["description"].strip()
        if not value:
            raise forms.ValidationError(_("Describe the problem."))
        return value


class PhotoForm(forms.Form):
    photo = forms.ImageField(
        label=_("Add a photo"),
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"}),
        help_text=_("JPEG, PNG or WebP, up to 10 MB."),
    )


class ResolveForm(forms.Form):
    resolution_note = forms.CharField(label=_("How was it resolved?"), widget=forms.Textarea(attrs={"rows": 3}))
