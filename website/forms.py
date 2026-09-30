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


# ------------------------------------------------------------------ EP08 (owner)

TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
PHOTO_HELP = _("JPEG, PNG or WebP, up to 10 MB. Large photos are scaled down.")


class RestaurantInfoForm(forms.Form):
    name = forms.CharField(label=_("Restaurant name"), max_length=200)
    tagline = forms.CharField(label=_("Tagline"), max_length=140, required=False,
                              help_text=_("One short line under the name, for example “Vietnamese street food”."))
    introduction = forms.CharField(label=_("Introduction"), max_length=1200, required=False,
                                   widget=forms.Textarea(attrs={"rows": 5}),
                                   help_text=_("Shown under “About us” on the homepage."))
    street_address = forms.CharField(label=_("Street address"), max_length=150, required=False)
    postal_code = forms.CharField(label=_("Postal code"), max_length=20, required=False)
    city = forms.CharField(label=_("City"), max_length=80, required=False)
    country = forms.CharField(label=_("Country"), max_length=80, required=False)
    phone = forms.CharField(label=_("Phone"), max_length=40, required=False,
                            widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}))
    email = forms.CharField(label=_("Email"), max_length=254, required=False,
                            widget=forms.TextInput(attrs={"type": "email", "autocomplete": "email"}))


class WeeklyHoursForm(forms.Form):
    """Two optional opening periods per weekday. A day with no times is closed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for d in range(7):
            for n in (1, 2):
                self.fields[f"d{d}_opens{n}"] = forms.TimeField(required=False, widget=TIME)
                self.fields[f"d{d}_closes{n}"] = forms.TimeField(required=False, widget=TIME)

    @staticmethod
    def initial_from(week):
        initial = {}
        for day in week:
            for n, p in enumerate(day["periods"][:2], start=1):
                initial[f"d{day['weekday']}_opens{n}"] = p.opens
                initial[f"d{day['weekday']}_closes{n}"] = p.closes
        return initial

    def week(self):
        data = self.cleaned_data
        return {d: [(data.get(f"d{d}_opens{n}"), data.get(f"d{d}_closes{n}")) for n in (1, 2)] for d in range(7)}

    def rows(self, labels):
        return [
            {"weekday": d, "label": labels[d], "field_name": f"day_{d}",
             "fields": [(self[f"d{d}_opens{n}"], self[f"d{d}_closes{n}"]) for n in (1, 2)]}
            for d in range(7)
        ]


class SpecialDayForm(forms.Form):
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label=_("Note (optional)"), max_length=80, required=False,
                           help_text=_("Shown to customers, for example “Christmas Eve”."))
    is_closed = forms.BooleanField(label=_("Closed all day"), required=False)
    opens = forms.TimeField(label=_("Opens"), required=False, widget=TIME)
    closes = forms.TimeField(label=_("Closes"), required=False, widget=TIME)
    opens_2 = forms.TimeField(label=_("Opens again (optional)"), required=False, widget=TIME)
    closes_2 = forms.TimeField(label=_("Closes"), required=False, widget=TIME)

    def periods(self):
        d = self.cleaned_data
        return [(d.get("opens"), d.get("closes")), (d.get("opens_2"), d.get("closes_2"))]


class PhotoForm(forms.Form):
    photo = forms.ImageField(label=_("Photo"), help_text=PHOTO_HELP)


class DescribedPhotoForm(forms.Form):
    photo = forms.ImageField(label=_("Photo"), help_text=PHOTO_HELP)
    alt = forms.CharField(label=_("Description"), max_length=200,
                          help_text=_("Describe the photo in a few words, for example “Dining room with window seats”. Screen readers read this aloud."))


class AltForm(forms.Form):
    alt = forms.CharField(label=_("Description"), max_length=200)
