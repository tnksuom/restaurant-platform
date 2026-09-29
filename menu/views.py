from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.permissions import manager_required

from . import services
from .forms import CategoryForm, ItemForm
from .models import MenuCategory, MenuItem


def _add_errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc.messages)


def _flash_error(request, exc):
    messages.error(request, " ".join(exc.messages))


@manager_required
def overview(request):
    restaurant = request.user.restaurant
    categories = list(MenuCategory.objects.filter(restaurant=restaurant).order_by("position", "id"))
    items = MenuItem.objects.filter(restaurant=restaurant).order_by("position", "id")
    by_category = {}
    for item in items:
        by_category.setdefault(item.category_id, []).append(item)
    sections = [{"category": c, "items": by_category.get(c.pk, [])} for c in categories]
    counts = {
        "published": sum(1 for i in items if i.is_published),
        "draft": sum(1 for i in items if not i.is_published),
        "unavailable": sum(1 for i in items if i.is_published and not i.is_available),
    }
    return render(
        request, "menu/overview.html", {"sections": sections, "counts": counts, "category_form": CategoryForm()}
    )


@manager_required
@require_POST
def category_create(request):
    form = CategoryForm(request.POST)
    if form.is_valid():
        try:
            category = services.create_category(request.user, form.cleaned_data["name"])
        except ValidationError as exc:
            _flash_error(request, exc)
        else:
            messages.success(request, _("Category “%(name)s” added.") % {"name": category.name})
    else:
        messages.error(request, _("Enter a category name."))
    return redirect("menu:overview")


@manager_required
def category_edit(request, pk):
    category = get_object_or_404(MenuCategory, pk=pk, restaurant=request.user.restaurant)
    form = CategoryForm(request.POST or None, initial={"name": category.name})
    if request.method == "POST" and form.is_valid():
        try:
            services.rename_category(request.user, category.pk, form.cleaned_data["name"])
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            messages.success(request, _("Category renamed."))
            return redirect("menu:overview")
    return render(
        request, "menu/category_form.html",
        {"form": form, "category": category, "item_count": category.items.count()},
    )


@manager_required
@require_POST
def category_delete(request, pk):
    category = get_object_or_404(MenuCategory, pk=pk, restaurant=request.user.restaurant)
    try:
        services.delete_category(request.user, category.pk)
    except ValidationError as exc:
        _flash_error(request, exc)
        return redirect("menu:category_edit", pk=category.pk)
    messages.success(request, _("Category deleted."))
    return redirect("menu:overview")


@manager_required
@require_POST
def category_move(request, pk, direction):
    if direction not in ("up", "down"):
        raise Http404
    get_object_or_404(MenuCategory, pk=pk, restaurant=request.user.restaurant)
    services.move_category(request.user, pk, direction)
    return redirect(reverse("menu:overview") + f"#category-{pk}")


def _item_form(request, item=None):
    categories = MenuCategory.objects.filter(restaurant=request.user.restaurant).order_by("position", "id")
    initial = {}
    if item:
        initial = {
            "name": item.name, "category": str(item.category_id), "description": item.description,
            "price": f"{item.price:.2f}", "allergens": item.allergens, "diet_labels": item.diet_labels,
            "status": item.status, "is_available": item.is_available, "when_unavailable": item.when_unavailable,
        }
    else:
        wanted = request.GET.get("category", "")
        if wanted.isdigit():
            initial["category"] = wanted
    return ItemForm(request.POST or None, request.FILES or None, initial=initial, categories=categories), categories


def _save(request, item):
    form, categories = _item_form(request, item)
    if not categories:
        messages.info(request, _("Add a category first, then add dishes to it."))
        return redirect("menu:overview")
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        try:
            saved = services.save_item(
                request.user, item, data, photo=data.pop("photo", None), remove_photo=data.pop("remove_photo", False)
            )
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            if saved.is_published:
                messages.success(request, _("“%(name)s” is saved and on the public menu.") % {"name": saved.name})
            else:
                messages.success(request, _("“%(name)s” is saved as a draft.") % {"name": saved.name})
            return redirect(reverse("menu:overview") + f"#item-{saved.pk}")
    return render(request, "menu/item_form.html", {"form": form, "item": item})


@manager_required
def item_create(request):
    return _save(request, None)


@manager_required
def item_edit(request, pk):
    item = get_object_or_404(MenuItem.objects.select_related("category"), pk=pk, restaurant=request.user.restaurant)
    return _save(request, item)


@manager_required
@require_POST
def item_availability(request, pk):
    item = get_object_or_404(MenuItem, pk=pk, restaurant=request.user.restaurant)
    available = request.POST.get("available") == "1"
    services.set_available(request.user, item.pk, available)
    messages.success(
        request,
        (_("“%(name)s” is available again.") if available else _("“%(name)s” is marked unavailable."))
        % {"name": item.name},
    )
    return redirect(reverse("menu:overview") + f"#item-{item.pk}")


@manager_required
@require_POST
def item_status(request, pk):
    item = get_object_or_404(MenuItem, pk=pk, restaurant=request.user.restaurant)
    status = request.POST.get("status", "")
    try:
        services.set_status(request.user, item.pk, status)
    except ValidationError as exc:
        _flash_error(request, exc)
    else:
        messages.success(
            request,
            (_("“%(name)s” is published.") if status == MenuItem.Status.PUBLISHED else _("“%(name)s” is back to draft."))
            % {"name": item.name},
        )
    return redirect(reverse("menu:overview") + f"#item-{item.pk}")


@manager_required
@require_POST
def item_move(request, pk, direction):
    if direction not in ("up", "down"):
        raise Http404
    get_object_or_404(MenuItem, pk=pk, restaurant=request.user.restaurant)
    services.move_item(request.user, pk, direction)
    return redirect(reverse("menu:overview") + f"#item-{pk}")


@manager_required
def item_delete(request, pk):
    item = get_object_or_404(MenuItem, pk=pk, restaurant=request.user.restaurant)
    if request.method == "POST":
        services.delete_item(request.user, item.pk)
        messages.success(request, _("“%(name)s” was deleted.") % {"name": item.name})
        return redirect("menu:overview")
    return render(request, "menu/item_delete.html", {"item": item})
