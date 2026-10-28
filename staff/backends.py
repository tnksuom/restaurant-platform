from django.contrib.auth.backends import ModelBackend

from .models import Employee, normalize_identifier


class EmployeeBackend(ModelBackend):
    """Case-insensitive login, scoped to the organization the request belongs to.

    Inside an organization (request.tenant) only that organization's staff can sign in;
    on the platform's own pages only platform administrators can. The same login name
    may therefore exist in many organizations.

    ModelBackend.get_user() re-reads the employee on every request and returns None for
    inactive accounts, so deactivation and role changes apply to signed-in sessions on
    their next request. tenants.middleware.TenantAccessMiddleware then checks that the
    session's person belongs to the organization of the host they are on.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        if request is not None and hasattr(request, "tenant"):
            tenant = request.tenant
        else:  # called outside a web request (tests, shell): the default organization, if any
            from django.conf import settings

            from tenants.models import Organization

            slug = settings.TENANT_DEFAULT_SLUG
            tenant = Organization.objects.filter(slug=slug).first() if slug else None
        qs = Employee.objects.filter(login_identifier=normalize_identifier(username))
        if tenant is not None:
            qs = qs.filter(organization=tenant, is_platform_admin=False)
        else:
            qs = qs.filter(organization__isnull=True, is_platform_admin=True)
        user = qs.first()
        if user is None:
            Employee().set_password(password)  # same work as a real check, against timing attacks
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
