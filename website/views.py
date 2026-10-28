import json

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from core.images import ImageStore
from core.permissions import manager_required, owner_required
from core.timeutils import business_today
from menu import services as menu_services
from menu.models import Allergen, DietLabel, MenuItem

from . import services
from .forms import (AltForm, DescribedPhotoForm, PhotoForm, PromotionForm, RestaurantInfoForm, SpecialDayForm,
                    WeeklyHoursForm)
from .models import GalleryImage, OpeningHours, PublicProfile, Promotion, SpecialDay


def _site(request):
    """Context shared by every public page."""
    restaurant = services.public_restaurant(request)
    if restaurant is None:
        return None
    profile = services.profile_for(restaurant)
    return {
        "site_restaurant": restaurant,
        "profile": profile,
        "status": services.open_status(restaurant),
        "map_url": services.map_url(profile),
    }


def _render(request, template, context):
    site = _site(request)
    if site is None:
        return render(request, "website/coming_soon.html", status=200)
    site.update(context)
    site["structured_data"] = _structured_data(request, site)
    return render(request, template, site)


def _structured_data(request, site):
    """schema.org Restaurant data so search engines show address and hours correctly."""
    restaurant, profile = site["site_restaurant"], site["profile"]
    data = {"@context": "https://schema.org", "@type": "Restaurant", "name": restaurant.name,
            "url": request.build_absolute_uri("/"), "servesCuisine": profile.tagline or None,
            "telephone": profile.phone or None, "email": profile.email or None,
            "hasMenu": request.build_absolute_uri("/menu/")}
    if profile.has_address:
        data["address"] = {"@type": "PostalAddress", "streetAddress": profile.street_address,
                           "postalCode": profile.postal_code, "addressLocality": profile.city,
                           "addressCountry": profile.country}
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    specs = []
    for day in services.weekly_hours(restaurant):
        for p in day["periods"]:
            specs.append({"@type": "OpeningHoursSpecification", "dayOfWeek": days[day["weekday"]],
                          "opens": p.opens.strftime("%H:%M"), "closes": p.closes.strftime("%H:%M")})
    if specs:
        data["openingHoursSpecification"] = specs
    special = []
    for day in services.upcoming_special_days(restaurant):
        stamp = day.date.isoformat()
        for p in day.periods or [None]:
            special.append({"@type": "OpeningHoursSpecification", "validFrom": stamp, "validThrough": stamp,
                            "opens": p.opens.strftime("%H:%M") if p else "00:00",
                            "closes": p.closes.strftime("%H:%M") if p else "00:00"})
    if special:
        data["specialOpeningHoursSpecification"] = special
    data = {k: v for k, v in data.items() if v}
    text = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return mark_safe(text)


@login_not_required
def home(request):
    restaurant = services.public_restaurant(request)
    promotions = services.active_promotions(restaurant) if restaurant else []
    featured = []
    if restaurant:
        featured = [
            i for i in MenuItem.objects.filter(restaurant=restaurant, status=MenuItem.Status.PUBLISHED, is_available=True)
            .exclude(photo_key="").select_related("category").order_by("category__position", "position")[:3]
        ]
    gallery = list(GalleryImage.objects.filter(restaurant=restaurant)) if restaurant else []
    return _render(request, "website/home.html",
                   {"promotions": promotions, "featured": featured, "gallery": gallery, "page": "home"})


@login_not_required
def menu(request):
    restaurant = services.public_restaurant(request)
    sections = menu_services.public_menu(restaurant) if restaurant else []
    used = {a for s in sections for i in s["items"] for a in i.diet_labels}
    legend = [(c, l) for c, l in DietLabel.choices if c in used]
    return _render(
        request, "website/menu.html",
        {"sections": sections, "legend": legend, "allergen_names": dict(Allergen.choices), "page": "menu"},
    )


@login_not_required
def contact(request):
    restaurant = services.public_restaurant(request)
    week = services.weekly_hours(restaurant) if restaurant else []
    special_days = services.upcoming_special_days(restaurant) if restaurant else []
    return _render(request, "website/contact.html", {"week": week, "special_days": special_days, "page": "contact"})


@login_not_required
def image(request, key):
    """Serve a public image by key. Photos of unpublished or hidden dishes are only shown to managers."""
    item = MenuItem.objects.filter(photo_key=key, restaurant__organization=request.tenant).first()
    if item is not None:
        if not menu_services.can_view_photo(request.user, item):
            raise Http404
    elif not services.is_public_site_image(key, request.tenant):
        raise Http404
    store = ImageStore(menu_services.public_store().root())
    try:
        fh = open(store.path(key), "rb")
    except (FileNotFoundError, ValueError):
        raise Http404
    response = FileResponse(fh, content_type=store.content_type(key))
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    # A key never changes content (a new upload gets a new key), so browsers may cache it.
    response["Cache-Control"] = "public, max-age=31536000, immutable" if (item is None or item.is_public) else "private, no-store"
    return response


# ------------------------------------------------------------------ promotions (managers)


@manager_required
def promotion_list(request):
    today = business_today(request.user.restaurant)
    promotions = list(Promotion.objects.filter(restaurant=request.user.restaurant).select_related("created_by"))
    groups = {"active": [], "upcoming": [], "unpublished": [], "expired": []}
    for p in promotions:
        groups[p.state_on(today)].append(p)
    return render(request, "website/promotion_list.html", {"groups": groups, "today": today})


@manager_required
def promotion_edit(request, pk=None):
    promotion = get_object_or_404(Promotion, pk=pk, restaurant=request.user.restaurant) if pk else None
    initial = {"starts_on": business_today(request.user.restaurant), "is_published": True}
    if promotion:
        initial = {"title": promotion.title, "body": promotion.body, "starts_on": promotion.starts_on,
                   "ends_on": promotion.ends_on, "is_published": promotion.is_published}
    form = PromotionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_promotion(request.user, promotion, **form.cleaned_data)
        except ValidationError as exc:
            for field, errors in exc.message_dict.items():
                form.add_error(field if field in form.fields else None, errors)
        else:
            messages.success(request, _("Promotion saved."))
            return redirect("website:promotions")
    return render(request, "website/promotion_form.html", {"form": form, "promotion": promotion})


@manager_required
def promotion_delete(request, pk):
    promotion = get_object_or_404(Promotion, pk=pk, restaurant=request.user.restaurant)
    if request.method == "POST":
        services.delete_promotion(request.user, promotion.pk)
        messages.success(request, _("Promotion deleted."))
        return redirect("website:promotions")
    return render(request, "website/promotion_delete.html", {"promotion": promotion})


# ------------------------------------------------------------------ EP08: restaurant administration (owner)


def _form_errors(form, exc):
    for field, errors in exc.message_dict.items():
        form.add_error(field if field in form.fields else None, errors)


@owner_required
def restaurant_info(request):
    restaurant = request.user.restaurant
    profile = services.profile_for(restaurant)
    initial = {"name": restaurant.name, **{f: getattr(profile, f) for f in services.PROFILE_FIELDS}}
    form = RestaurantInfoForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_restaurant_info(request.user, **form.cleaned_data)
        except ValidationError as exc:
            _form_errors(form, exc)
        else:
            messages.success(request, _("Restaurant details saved. The public website shows them now."))
            return redirect("website:info")
    return render(request, "website/manage_info.html", {"form": form})


@owner_required
def opening_hours(request):
    restaurant = request.user.restaurant
    labels = {d: str(label) for d, label in OpeningHours.WEEKDAYS}
    form = WeeklyHoursForm(request.POST or None, initial=WeeklyHoursForm.initial_from(services.weekly_hours(restaurant)))
    day_errors = {}
    if request.method == "POST" and form.is_valid():
        try:
            services.save_weekly_hours(request.user, form.week())
        except ValidationError as exc:
            day_errors = {k: v for k, v in exc.message_dict.items()}
        else:
            messages.success(request, _("Opening hours saved."))
            return redirect("website:hours")
    rows = form.rows(labels)
    for row in rows:
        row["errors"] = day_errors.get(row["field_name"], [])
        row["field_errors"] = [e for pair in row["fields"] for f in pair for e in f.errors]
    today = business_today(restaurant)
    return render(request, "website/manage_hours.html", {
        "form": form, "rows": rows, "has_errors": bool(day_errors) or bool(form.errors),
        "special_days": services.upcoming_special_days(restaurant, today, days=366),
        "past_special_days": list(SpecialDay.objects.filter(restaurant=restaurant, date__lt=today).order_by("-date")[:5]),
    })


@owner_required
def special_day_edit(request, pk=None):
    special = get_object_or_404(SpecialDay, pk=pk, restaurant=request.user.restaurant) if pk else None
    initial = {}
    if special:
        initial = {"date": special.date, "note": special.note, "is_closed": special.is_closed,
                   "opens": special.opens, "closes": special.closes,
                   "opens_2": special.opens_2, "closes_2": special.closes_2}
    elif request.GET.get("date"):
        initial = {"date": request.GET["date"]}
    form = SpecialDayForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.save_special_day(request.user, special, date=d["date"], is_closed=d["is_closed"],
                                      note=d["note"], periods=form.periods())
        except ValidationError as exc:
            _form_errors(form, exc)
        else:
            messages.success(request, _("Special hours saved for %(d)s.") % {"d": d["date"].strftime("%d.%m.%Y")})
            return redirect("website:hours")
    return render(request, "website/manage_special_day.html", {"form": form, "special": special})


@owner_required
def special_day_delete(request, pk):
    special = get_object_or_404(SpecialDay, pk=pk, restaurant=request.user.restaurant)
    if request.method == "POST":
        services.delete_special_day(request.user, special.pk)
        messages.success(request, _("Special hours removed. The regular hours apply on that date again."))
    return redirect("website:hours")


@owner_required
def site_images(request):
    restaurant = request.user.restaurant
    forms = {"logo": PhotoForm(prefix="logo"), "hero": DescribedPhotoForm(prefix="hero"),
             "hero_alt": AltForm(prefix="hero_alt", initial={"alt": services.profile_for(restaurant).hero_alt}),
             "gallery": DescribedPhotoForm(prefix="gallery")}
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            done = _image_action(request, action, forms)
        except ValidationError as exc:
            target = {"logo_upload": "logo", "hero_upload": "hero", "hero_alt": "hero_alt",
                      "gallery_add": "gallery"}.get(action)
            if target:
                _form_errors(forms[target], exc)
            else:
                for message in exc.messages:
                    messages.error(request, message)
            done = None
        except (GalleryImage.DoesNotExist, ValueError, TypeError):
            raise Http404
        if done:
            messages.success(request, done)
            return redirect("website:images")
    profile = services.profile_for(restaurant)
    gallery = list(GalleryImage.objects.filter(restaurant=restaurant))
    return render(request, "website/manage_images.html", {
        "profile": profile, "forms": forms, "gallery": gallery,
        "gallery_full": len(gallery) >= GalleryImage.MAX_PER_RESTAURANT, "gallery_max": GalleryImage.MAX_PER_RESTAURANT,
    })


def _image_action(request, action, forms):
    """Run one action from the images page. Returns a success message, or None if a form is invalid."""
    user, post, files = request.user, request.POST, request.FILES
    if action == "logo_upload":
        forms["logo"] = form = PhotoForm(post, files, prefix="logo")
        if form.is_valid():
            services.replace_site_image(user, "logo", form.cleaned_data["photo"])
            return _("Logo saved.")
    elif action == "hero_upload":
        forms["hero"] = form = DescribedPhotoForm(post, files, prefix="hero")
        if form.is_valid():
            services.replace_site_image(user, "hero", form.cleaned_data["photo"], form.cleaned_data["alt"])
            return _("Homepage photo saved.")
    elif action == "hero_alt":
        forms["hero_alt"] = form = AltForm(post, prefix="hero_alt")
        if form.is_valid():
            services.set_hero_alt(user, form.cleaned_data["alt"])
            return _("Description saved.")
    elif action in ("logo_remove", "hero_remove"):
        services.remove_site_image(user, action.split("_")[0])
        return _("Photo removed from the website.")
    elif action == "gallery_add":
        forms["gallery"] = form = DescribedPhotoForm(post, files, prefix="gallery")
        if form.is_valid():
            services.add_gallery_image(user, form.cleaned_data["photo"], form.cleaned_data["alt"])
            return _("Photo added to the gallery.")
    elif action == "gallery_alt":
        form = AltForm(post)
        if not form.is_valid():
            raise ValidationError(form.errors["alt"])
        services.describe_gallery_image(user, int(post.get("image")), form.cleaned_data["alt"])
        return _("Description saved.")
    elif action in ("gallery_up", "gallery_down"):
        services.move_gallery_image(user, int(post.get("image")), action.split("_")[1])
        return _("Order saved.")
    elif action == "gallery_delete":
        services.delete_gallery_image(user, int(post.get("image")))
        return _("Photo removed from the website.")
    else:
        raise ValueError(action)
    return None
