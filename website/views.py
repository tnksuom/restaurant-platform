import json

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from core.images import ImageStore
from core.permissions import manager_required
from core.timeutils import business_today
from menu import services as menu_services
from menu.models import Allergen, DietLabel, MenuItem

from . import services
from .forms import PromotionForm
from .models import PublicProfile, Promotion


def _site(request):
    """Context shared by every public page."""
    restaurant = services.public_restaurant()
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
    data = {k: v for k, v in data.items() if v}
    text = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return mark_safe(text)


@login_not_required
def home(request):
    restaurant = services.public_restaurant()
    promotions = services.active_promotions(restaurant) if restaurant else []
    featured = []
    if restaurant:
        featured = [
            i for i in MenuItem.objects.filter(restaurant=restaurant, status=MenuItem.Status.PUBLISHED, is_available=True)
            .exclude(photo_key="").select_related("category").order_by("category__position", "position")[:3]
        ]
    return _render(request, "website/home.html", {"promotions": promotions, "featured": featured, "page": "home"})


@login_not_required
def menu(request):
    restaurant = services.public_restaurant()
    sections = menu_services.public_menu(restaurant) if restaurant else []
    used = {a for s in sections for i in s["items"] for a in i.diet_labels}
    legend = [(c, l) for c, l in DietLabel.choices if c in used]
    return _render(
        request, "website/menu.html",
        {"sections": sections, "legend": legend, "allergen_names": dict(Allergen.choices), "page": "menu"},
    )


@login_not_required
def contact(request):
    restaurant = services.public_restaurant()
    week = services.weekly_hours(restaurant) if restaurant else []
    return _render(request, "website/contact.html", {"week": week, "page": "contact"})


@login_not_required
def image(request, key):
    """Serve a public image by key. Photos of unpublished or hidden dishes are only shown to managers."""
    item = MenuItem.objects.filter(photo_key=key).first()
    if item is not None:
        if not menu_services.can_view_photo(request.user, item):
            raise Http404
    elif not PublicProfile.objects.filter(logo_key=key).exists() and not PublicProfile.objects.filter(hero_key=key).exists():
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
