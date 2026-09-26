from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from .models import Role


class LoginForm(AuthenticationForm):
    error_messages = {
        # Generic on purpose: never reveals whether the account exists or is disabled.
        "invalid_login": _("Those login details did not work. Check them and try again."),
        "inactive": _("Those login details did not work. Check them and try again."),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Login name or email")
        self.fields["username"].widget.attrs.update({"autocapitalize": "none", "autocomplete": "username"})


class EmployeeCreateForm(forms.Form):
    display_name = forms.CharField(label=_("Name"), max_length=150)
    login_identifier = forms.CharField(
        label=_("Login name or email"), max_length=150, help_text=_("Not case-sensitive. Must be unique.")
    )
    phone = forms.CharField(label=_("Contact number (optional)"), max_length=40, required=False)
    role = forms.ChoiceField(label=_("Role"))

    def __init__(self, *args, roles=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [(r, Role(r).label) for r in roles]
        self.fields["role"].initial = Role.EMPLOYEE


class EmployeeDetailsForm(forms.Form):
    display_name = forms.CharField(label=_("Name"), max_length=150)
    phone = forms.CharField(label=_("Contact number (optional)"), max_length=40, required=False)


class RoleForm(forms.Form):
    role = forms.ChoiceField(label=_("Role"))

    def __init__(self, *args, roles=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [(r, Role(r).label) for r in roles]
