"""Pages inside an organization: locations, location switching, plan and modules, leaving a workspace."""
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.permissions import owner_required
from core.models import Restaurant

from . import services
from .forms import LocationForm, ModulesForm
from .middleware import SESSION_KEY
from .models import Plan


def _errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errs in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errs)
    else:
        form.add_error(None, exc.messages)


@require_POST
def switch_location(request):
    try:
        location = services.switch_location(request.user, int(request.POST.get("location", 0)))
    except (ValueError, PermissionDenied):
        raise PermissionDenied
    messages.success(request, _("You are now working at %(name)s.") % {"name": location.name})
    nxt = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = "/app/"
    return redirect(nxt)


@owner_required
def locations(request):
    org = request.tenant
    add_form = LocationForm(request.POST or None, prefix="add")
    if request.method == "POST" and request.POST.get("action") == "add" and add_form.is_valid():
        try:
            location = services.add_location(request.user, add_form.cleaned_data["name"])
        except ValidationError as exc:
            _errors(add_form, exc)
        else:
            messages.success(request, _("Location “%(name)s” added.") % {"name": location.name})
            return redirect("tenants:locations")
    if request.method == "POST" and request.POST.get("action") == "rename":
        form = LocationForm(request.POST)
        if form.is_valid():
            try:
                services.rename_location(request.user, int(request.POST.get("location", 0)), form.cleaned_data["name"])
            except (ValueError, Restaurant.DoesNotExist):
                raise PermissionDenied
            messages.success(request, _("Location renamed."))
            return redirect("tenants:locations")
    rows = (Restaurant.objects.filter(organization=org).annotate(staff=Count("employees", distinct=True))
            .order_by("name", "pk"))
    usage = services.organization_usage(org)
    return render(request, "tenants/locations.html", {
        "rows": rows, "add_form": add_form, "usage": usage,
        "can_add": usage["locations"] < usage["max_locations"],
    })


@owner_required
def account(request):
    org = request.tenant
    modules_form = ModulesForm(request.POST or None, organization=org)
    if request.method == "POST" and request.POST.get("action") == "modules" and modules_form.is_valid():
        services.set_modules(request.user, org, modules_form.enabled())
        messages.success(request, _("Modules saved."))
        return redirect("tenants:account")
    if request.method == "POST" and request.POST.get("action") == "plan":
        plan = Plan.objects.filter(pk=request.POST.get("plan"), is_public=True).first()
        if plan is None:
            raise PermissionDenied
        try:
            services.change_plan(request.user, org, plan)
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
        else:
            messages.success(request, _("Your plan is now %(plan)s.") % {"plan": plan.name})
        return redirect("tenants:account")
    return render(request, "tenants/account.html", {
        "org": org, "modules_form": modules_form, "usage": services.organization_usage(org),
        "plans": Plan.objects.filter(is_public=True), "today": timezone.now().date(),
        "can_change_plan": org.status == org.Status.TRIAL,
    })


@login_not_required
@require_POST
def leave_workspace(request):
    """Single-domain hosts remember the workspace in the session; this forgets it."""
    logout(request)
    request.session.pop(SESSION_KEY, None)
    return redirect("/")
