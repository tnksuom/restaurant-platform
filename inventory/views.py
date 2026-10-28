from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.permissions import is_manager, manager_required
from core.timeutils import day_range_utc
from staff.models import Employee

from . import services
from .forms import ItemForm, MovementForm
from .models import Category, InventoryItem, MovementType, StockMovement


def _add_errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc.messages)


def item_list(request):
    manager = is_manager(request.user)
    base = InventoryItem.objects.filter(restaurant=request.user.restaurant)
    qs = base
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(supplier_name__icontains=q))
    category = request.GET.get("category", "")
    if category in Category.values:
        qs = qs.filter(category=category)
    status = request.GET.get("status", "active") if manager else "active"
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    low_only = request.GET.get("low") == "1"
    if low_only:
        qs = qs.filter(current_quantity__lte=F("minimum_quantity"), is_active=True)
    low_count = base.filter(is_active=True, current_quantity__lte=F("minimum_quantity")).count()
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "inventory/list.html",
        {
            "page": page, "q": q, "category": category, "categories": Category.choices,
            "status": status, "low_only": low_only, "low_count": low_count,
        },
    )


@manager_required
def item_create(request):
    form = ItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            item = services.create_item(request.user, **form.cleaned_data)
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            messages.success(request, _("%(name)s was added.") % {"name": item.name})
            return redirect("inventory:detail", pk=item.pk)
    return render(request, "inventory/item_form.html", {"form": form, "title": _("Add inventory item")})


@manager_required
def item_edit(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk, restaurant=request.user.restaurant)
    unit_locked = item.movements.exists()
    initial = {
        "name": item.name, "category": item.category, "unit": item.unit,
        "minimum_quantity": item.minimum_quantity.normalize(), "supplier_name": item.supplier_name,
    }
    form = ItemForm(request.POST or None, initial=initial, editing=True, unit_locked=unit_locked)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_item(request.user, item, **form.cleaned_data)
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            messages.success(request, _("Item saved. Past stock records are unchanged."))
            return redirect("inventory:detail", pk=item.pk)
    return render(request, "inventory/item_form.html", {"form": form, "title": _("Edit %(name)s") % {"name": item.name}, "item": item})


@manager_required
@require_POST
def item_set_active(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk, restaurant=request.user.restaurant)
    active = request.POST.get("active") == "1"
    services.set_item_active(request.user, item, active)
    messages.success(request, _("Item reactivated.") if active else _("Item deactivated. Its history is kept."))
    return redirect("inventory:detail", pk=item.pk)


def item_detail(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk, restaurant=request.user.restaurant)
    manager = is_manager(request.user)
    form = MovementForm(request.POST or None, manager=manager)
    if request.method == "POST":
        if form.is_valid():
            data = form.cleaned_data
            try:
                movement, created = services.post_movement(
                    request.user, item.pk, data["type"], data["quantity"], note=data["note"],
                    waste_reason=data["waste_reason"], idempotency_key=data["idempotency_key"],
                )
            except ValidationError as exc:
                _add_errors(form, exc)
            else:
                item.refresh_from_db()
                if created:
                    messages.success(
                        request,
                        _("Saved: %(type)s %(qty)s %(unit)s. Stock is now %(bal)s %(unit)s.")
                        % {
                            "type": movement.get_type_display(), "qty": abs(movement.signed_quantity).normalize(),
                            "unit": item.unit, "bal": item.current_quantity.normalize(),
                        },
                    )
                else:
                    messages.info(request, _("This entry was already saved. It was not recorded twice."))
                return redirect("inventory:detail", pk=item.pk)
        # keep the same idempotency key only if nothing was saved
    movements = item.movements.select_related("actor").order_by("-recorded_at", "-id")
    page = Paginator(movements, 25).get_page(request.GET.get("page"))
    return render(request, "inventory/detail.html", {"item": item, "form": form, "page": page})


@manager_required
def history(request):
    restaurant = request.user.restaurant
    qs = StockMovement.objects.filter(restaurant=restaurant).select_related("item", "actor")
    filters = {k: request.GET.get(k, "") for k in ("item", "type", "actor", "from", "to")}
    if filters["item"].isdigit():
        qs = qs.filter(item_id=int(filters["item"]))
    if filters["type"] in MovementType.values:
        qs = qs.filter(type=filters["type"])
    if filters["actor"].isdigit():
        qs = qs.filter(actor_id=int(filters["actor"]))
    date_from, date_to = parse_date(filters["from"] or ""), parse_date(filters["to"] or "")
    if date_from or date_to:
        start, end = day_range_utc(date_from or date_to, date_to or date_from, restaurant.tz)
        if date_from:
            qs = qs.filter(recorded_at__gte=start)
        if date_to:
            qs = qs.filter(recorded_at__lt=end)
    page = Paginator(qs.order_by("-recorded_at", "-id"), 50).get_page(request.GET.get("page"))
    return render(
        request,
        "inventory/history.html",
        {
            "page": page, "filters": filters, "types": MovementType.choices,
            "items": InventoryItem.objects.filter(restaurant=restaurant).order_by("name"),
            "staff": Employee.objects.working_at(restaurant).order_by("display_name"),
        },
    )
