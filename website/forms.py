from django import forms
from django.utils.translation import gettext_lazy as _


class PromotionForm(forms.Form):
    title = forms.CharField(label=_("Title"), max_length=120, help_text=_("For example: Lunch deal – soup and bánh mì 12 €"))
    body = forms.CharField(label=_("Text"), required=False, max_length=800, widget=forms.Textarea(attrs={"rows": 4}))
    starts_on = forms.DateField(label=_("First day"), widget=forms.DateInput(attrs={"type": "date"}))
    ends_on = forms.DateField(
        label=_("Last day"), required=False, widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("The promotion disappears from the website after this day. Leave empty to keep it running."),
    )
    is_published = forms.BooleanField(label=_("Show on the website"), required=False)
