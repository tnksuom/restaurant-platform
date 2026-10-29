from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import MODULES, Organization, Plan

TIME_ZONES = [
    ("Europe/Helsinki", "Europe/Helsinki"), ("Europe/Stockholm", "Europe/Stockholm"),
    ("Europe/Tallinn", "Europe/Tallinn"), ("Europe/Oslo", "Europe/Oslo"), ("Europe/Copenhagen", "Europe/Copenhagen"),
    ("Europe/Berlin", "Europe/Berlin"), ("Europe/London", "Europe/London"), ("Asia/Ho_Chi_Minh", "Asia/Ho_Chi_Minh"),
]
MODULE_LABELS = {
    "timeclock": _("Time clock"), "checklists": _("Checklists"), "inventory": _("Inventory"),
    "menu": _("Food menu"), "website": _("Public website"),
}


class PlanChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, plan):
        return f"{plan.name} · {plan.monthly_price} €"


class SignupForm(forms.Form):
    name = forms.CharField(label=_("Business name"), max_length=120)
    slug = forms.CharField(
        label=_("Web address"), max_length=40,
        help_text=_("Lowercase letters, numbers and hyphens. Your staff use it to find your workspace."),
        widget=forms.TextInput(attrs={"autocapitalize": "none", "spellcheck": "false"}),
    )
    location_name = forms.CharField(label=_("First location name (optional)"), max_length=200, required=False,
                                    help_text=_("For example “City centre”. Leave empty to use the business name."))
    time_zone = forms.ChoiceField(label=_("Time zone"), choices=TIME_ZONES, initial="Europe/Helsinki")
    plan = PlanChoiceField(label=_("Plan"), queryset=Plan.objects.filter(is_public=True), empty_label=None,
                           widget=forms.RadioSelect)
    owner_name = forms.CharField(label=_("Your name"), max_length=150)
    owner_login = forms.CharField(label=_("Your login name or email"), max_length=150,
                                  widget=forms.TextInput(attrs={"autocapitalize": "none", "autocomplete": "username"}))
    contact_email = forms.EmailField(label=_("Contact email"), required=False,
                                     help_text=_("Where we can reach you about your account."))
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=_("Password again"),
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_slug(self):
        return self.cleaned_data["slug"].strip().lower()

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("password2"):
            self.add_error("password2", _("The two passwords are different."))
        elif data.get("password"):
            try:
                validate_password(data["password"])
            except ValidationError as exc:
                self.add_error("password", exc)
        return data


class FindWorkspaceForm(forms.Form):
    slug = forms.CharField(label=_("Workspace address"), max_length=40,
                           widget=forms.TextInput(attrs={"autocapitalize": "none", "spellcheck": "false",
                                                         "autofocus": True}))

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip().lower()
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            raise ValidationError(_("No workspace has this address. Check the spelling or ask your manager."))
        self.organization = org
        return slug


class LocationForm(forms.Form):
    name = forms.CharField(label=_("Location name"), max_length=200)


class ModulesForm(forms.Form):
    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        for m in MODULES:
            allowed = organization.plan.allows_module(m)
            self.fields[m] = forms.BooleanField(
                label=MODULE_LABELS[m], required=False, initial=organization.module_enabled(m), disabled=not allowed,
                help_text="" if allowed else _("Not included in the plan."),
            )

    def enabled(self):
        return {m for m, on in self.cleaned_data.items() if on}


class PlatformOrganizationForm(forms.Form):
    plan = PlanChoiceField(label=_("Plan"), queryset=Plan.objects.all(), empty_label=None)


class StatusForm(forms.Form):
    status = forms.ChoiceField(label=_("Status"), choices=Organization.Status.choices)
    note = forms.CharField(label=_("Reason / note"), max_length=200, required=False)


class TrialForm(forms.Form):
    until = forms.DateField(label=_("Trial ends on"), widget=forms.DateInput(attrs={"type": "date"}))
