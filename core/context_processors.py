from django.conf import settings

from .permissions import is_manager, is_owner


def navigation(request):
    context = {"demo_mode": settings.DEMO_MODE}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or user.is_platform_admin:
        return context
    context.update({"is_manager": is_manager(user), "is_owner": is_owner(user), "restaurant": user.restaurant})
    return context
