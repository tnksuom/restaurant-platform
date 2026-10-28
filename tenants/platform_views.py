"""The platform's own pages, served when the host names no organization:
landing page, sign-up, workspace finder and platform administration."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.permissions import platform_admin_required
from staff.forms import LoginForm

from . import services
from .forms import FindWorkspaceForm, ModulesForm, PlatformOrganizationForm, SignupForm, StatusForm, TrialForm
from .middleware import SESSION_KEY, tenant_url
from .models import Organization, Plan


def _errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errs in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errs)
    else:
        form.add_error(None, exc.messages)


def _open_workspace(request, org, path="/login/"):
    """Send the visitor into an organization: its sub-domain, or remember it in the session."""
    if settings.TENANT_SUBDOMAINS:
        return redirect(tenant_url(org, path))
    request.session[SESSION_KEY] = org.slug
    return redirect(path)


@login_not_required
def landing(request):
    demo = []
    if settings.DEMO_MODE:
        from core.demo_content import DEMO_ORGANIZATIONS

        demo = list(Organization.objects.filter(slug__in=[d["slug"] for d in DEMO_ORGANIZATIONS]).order_by("pk"))
    return render(request, "tenants/platform/landing.html", {
        "plans": Plan.objects.filter(is_public=True), "trial_days": settings.TRIAL_DAYS, "demo_orgs": demo,
    })


@login_not_required
def signup(request):
    chosen = Plan.objects.filter(is_public=True, code=request.GET.get("plan")).first()
    form = SignupForm(request.POST or None, initial={"plan": chosen or Plan.objects.filter(is_public=True).first()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            org, _location, owner = services.create_organization(
                name=d["name"], slug=d["slug"], plan=d["plan"], location_name=d["location_name"],
                owner_name=d["owner_name"], owner_login=d["owner_login"], owner_password=d["password"],
                time_zone=d["time_zone"], contact_email=d["contact_email"],
            )
        except ValidationError as exc:
            _errors(form, exc)
        else:
            if settings.TENANT_SUBDOMAINS:
                return render(request, "tenants/platform/signup_done.html", {
                    "org": org, "owner": owner, "login_url": tenant_url(org, "/login/"),
                })
            request.session[SESSION_KEY] = org.slug
            login(request, owner, backend="staff.backends.EmployeeBackend")
            return redirect("/app/")
    return render(request, "tenants/platform/signup.html", {"form": form, "trial_days": settings.TRIAL_DAYS})


@login_not_required
def find_workspace(request):
    form = FindWorkspaceForm(request.POST or request.GET or None) if (request.method == "POST" or "slug" in request.GET) \
        else FindWorkspaceForm()
    if form.is_bound and form.is_valid():
        return _open_workspace(request, form.organization)
    return render(request, "tenants/platform/find.html", {"form": form})


class PlatformLoginView(auth_views.LoginView):
    authentication_form = LoginForm
    template_name = "tenants/platform/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        return reverse("platform:home")


@require_POST
def platform_logout(request):
    logout(request)
    return redirect("login")


@platform_admin_required
def home(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    orgs = (Organization.objects.select_related("plan")
            .annotate(locations=Count("restaurants", distinct=True),
                      staff=Count("employees", filter=Q(employees__is_active=True), distinct=True))
            .order_by("-created_at"))
    if q:
        orgs = orgs.filter(Q(name__icontains=q) | Q(slug__icontains=q))
    if status in Organization.Status.values:
        orgs = orgs.filter(status=status)
    today = timezone.now().date()
    counts = {s: Organization.objects.filter(status=s).count() for s in Organization.Status.values}
    expired = [o for o in Organization.objects.filter(status=Organization.Status.TRIAL) if not o.is_usable_on(today)]
    return render(request, "tenants/platform/home.html", {
        "orgs": orgs, "q": q, "status": status, "statuses": Organization.Status.choices, "counts": counts,
        "expired_trials": len(expired), "today": today,
    })


@platform_admin_required
def organization_detail(request, pk):
    org = get_object_or_404(Organization.objects.select_related("plan"), pk=pk)
    action = request.POST.get("action") if request.method == "POST" else None
    plan_form = PlatformOrganizationForm(request.POST if action == "plan" else None, initial={"plan": org.plan})
    status_form = StatusForm(request.POST if action == "status" else None,
                             initial={"status": org.status, "note": org.status_note})
    trial_form = TrialForm(request.POST if action == "trial" else None, initial={"until": org.trial_ends_on})
    modules_form = ModulesForm(request.POST if action == "modules" else None, organization=org)
    try:
        if action == "plan" and plan_form.is_valid():
            services.change_plan(request.user, org, plan_form.cleaned_data["plan"])
            messages.success(request, _("Plan changed."))
            return redirect("platform:organization", pk=org.pk)
        if action == "status" and status_form.is_valid():
            services.set_status(request.user, org, status_form.cleaned_data["status"], status_form.cleaned_data["note"])
            messages.success(request, _("Status changed."))
            return redirect("platform:organization", pk=org.pk)
        if action == "trial" and trial_form.is_valid():
            services.extend_trial(request.user, org, trial_form.cleaned_data["until"])
            messages.success(request, _("Trial extended."))
            return redirect("platform:organization", pk=org.pk)
        if action == "modules" and modules_form.is_valid():
            services.set_modules(request.user, org, modules_form.enabled())
            messages.success(request, _("Modules saved."))
            return redirect("platform:organization", pk=org.pk)
    except ValidationError as exc:
        target = {"plan": plan_form, "status": status_form, "trial": trial_form}.get(action, plan_form)
        _errors(target, exc)
    return render(request, "tenants/platform/organization.html", {
        "org": org, "usage": services.organization_usage(org), "plan_form": plan_form, "status_form": status_form,
        "trial_form": trial_form, "modules_form": modules_form, "events": org.events.select_related("actor")[:30],
        "locations": org.restaurants.order_by("name"), "owners": org.employees.filter(role="OWNER", is_active=True),
        "open_url": tenant_url(org, "/login/") if settings.TENANT_SUBDOMAINS else None, "today": timezone.now().date(),
    })


@platform_admin_required
def organization_create(request):
    form = SignupForm(request.POST or None)
    form.fields["plan"].queryset = Plan.objects.all()
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            org, _l, _o = services.create_organization(
                name=d["name"], slug=d["slug"], plan=d["plan"], location_name=d["location_name"],
                owner_name=d["owner_name"], owner_login=d["owner_login"], owner_password=d["password"],
                time_zone=d["time_zone"], contact_email=d["contact_email"], actor=request.user,
            )
        except ValidationError as exc:
            _errors(form, exc)
        else:
            messages.success(request, _("%(name)s was created.") % {"name": org.name})
            return redirect("platform:organization", pk=org.pk)
    return render(request, "tenants/platform/organization_form.html", {"form": form})
