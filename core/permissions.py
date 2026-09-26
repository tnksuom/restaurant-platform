"""Role checks. Every view calls these on the server; hidden buttons are only cosmetic."""
from functools import wraps

from django.core.exceptions import PermissionDenied

OWNER = "OWNER"
MANAGER = "MANAGER"
EMPLOYEE = "EMPLOYEE"
MANAGEMENT_ROLES = (OWNER, MANAGER)


def is_manager(user):
    return bool(user.is_authenticated and user.is_active and user.role in MANAGEMENT_ROLES)


def is_owner(user):
    return bool(user.is_authenticated and user.is_active and user.role == OWNER)


def require_manager(user):
    if not is_manager(user):
        raise PermissionDenied


def manager_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        require_manager(request.user)
        return view(request, *args, **kwargs)

    return wrapper
