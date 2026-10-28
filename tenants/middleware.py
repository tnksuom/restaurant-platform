"""Which organization does this request belong to, and may it go ahead?

TenantMiddleware runs before authentication and sets request.tenant from the host name
(<slug>.<PLATFORM_DOMAIN>) or, on hosts without wildcard sub-domains, from the workspace
remembered in the session. Requests without an organization use the platform's own
URLs (landing page, sign-up, platform administration).

TenantAccessMiddleware runs after authentication and enforces isolation: a signed-in
person is only ever served inside their own organization, at a location they work at,
while the organization's subscription is usable and the module is switched on.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Organization

SESSION_KEY = "tenant_slug"
PLATFORM_URLCONF = "config.urls_platform"
TENANT_URLCONF = "config.urls"

# URL namespace -> module that has to be switched on.
MODULE_OF_NAMESPACE = {
    "timeclock": "timeclock",
    "checklists": "checklists",
    "inventory": "inventory",
    "menu": "menu",
    "website": "website",
}
# Pages that stay reachable while an organization's trial has ended or it is suspended.
ALWAYS_OPEN = {"login", "logout", "set_language", "leave_workspace", "account", "set_password"}


def _host(request):
    return request.get_host().rsplit(":", 1)[0].lower().rstrip(".")


def _org(slug):
    if not slug:
        return None
    return Organization.objects.select_related("plan").filter(slug=slug).first()


def resolve_tenant(request):
    """(organization or None, how it was found)."""
    base = settings.PLATFORM_DOMAIN
    if settings.TENANT_SUBDOMAINS:
        host = _host(request)
        if host.endswith("." + base):
            label = host[: -(len(base) + 1)]
            if label == "www":
                return None, "platform"
            org = _org(label) if "." not in label else None
            return (org, "subdomain") if org else (None, "unknown")
    # Links handed out on single-domain hosts (password set-up links) carry ?workspace=<slug>.
    wanted = request.GET.get("workspace")
    if wanted and _org(wanted):
        request.session[SESSION_KEY] = wanted
    slug = request.session.get(SESSION_KEY)
    if slug:
        org = _org(slug)
        if org:
            return org, "session"
        request.session.pop(SESSION_KEY, None)
    if settings.TENANT_DEFAULT_SLUG:
        org = _org(settings.TENANT_DEFAULT_SLUG)
        if org:
            return org, "default"
    return None, "platform"


def tenant_url(org, path="/"):
    """Absolute URL of a page inside an organization when sub-domains are in use, else the path."""
    if settings.TENANT_SUBDOMAINS:
        scheme = "http" if settings.DEBUG else "https"
        return f"{scheme}://{org.slug}.{settings.PLATFORM_DOMAIN}{path}"
    return path


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        org, source = resolve_tenant(request)
        request.tenant, request.tenant_source = org, source
        if org is None:
            request.urlconf = PLATFORM_URLCONF
            if source == "unknown":
                return render(request, "tenants/unknown_workspace.html", status=404)
        else:
            request.urlconf = TENANT_URLCONF
        return self.get_response(request)


class TenantAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = request.tenant
        user = request.user
        if user.is_authenticated:
            if user.is_platform_admin:
                if tenant is not None:
                    logout(request)  # platform admins never act inside a customer's organization
            elif tenant is None or user.organization_id != tenant.pk:
                logout(request)
            elif not self._ensure_location(request, user):
                logout(request)
                messages.error(request, _("Your account is not linked to any location. Ask your manager."))
        if tenant is not None:
            blocked = self._gate(request, tenant)
            if blocked is not None:
                return blocked
        return self.get_response(request)

    @staticmethod
    def _ensure_location(request, user):
        """Keep the person's current location one they may work at."""
        if user.restaurant_id and user.works_at(user.restaurant):
            return True
        first = user.allowed_locations().first()
        if first is None:
            return False
        user.restaurant = first
        user.save(update_fields=["restaurant", "updated_at"])
        return True

    @staticmethod
    def _gate(request, tenant):
        try:
            match = resolve(request.path_info, urlconf=request.urlconf)
        except Resolver404:
            return None
        name, namespace = match.url_name, match.namespace
        if name in ALWAYS_OPEN:
            return None
        if not tenant.is_usable_on(timezone.now().date()):
            return render(request, "tenants/paused.html", {"tenant": tenant}, status=403)
        module = MODULE_OF_NAMESPACE.get(namespace)
        if module and not tenant.module_enabled(module):
            if namespace == "website" and name == "image" and tenant.module_enabled("menu"):
                return None  # dish photos in the menu editor are served by the website app
            if namespace == "website" and name == "home":
                return redirect("today")
            raise Http404
        return None
