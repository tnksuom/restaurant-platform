"""Role checks. Every view calls these on the server; hidden buttons are only cosmetic."""
from functools import wraps

from django.core.exceptions import PermissionDenied

OWNER = "OWNER"
MANAGER = "MANAGER"
EMPLOYEE = "EMPLOYEE"
MANAGEMENT_ROLES = (OWNER, MANAGER)


def _tenant_user(user):
    return bool(user.is_authenticated and user.is_active and not getattr(user, "is_platform_admin", False))


def is_manager(user):
    return _tenant_user(user) and user.role in MANAGEMENT_ROLES


def is_owner(user):
    return _tenant_user(user) and user.role == OWNER


def is_platform_admin(user):
    return bool(user.is_authenticated and user.is_active and getattr(user, "is_platform_admin", False))


def platform_admin_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def require_manager(user):
    if not is_manager(user):
        raise PermissionDenied


def manager_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        require_manager(request.user)
        return view(request, *args, **kwargs)

    return wrapper


def owner_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_owner(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper
