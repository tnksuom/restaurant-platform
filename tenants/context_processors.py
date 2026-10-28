from django.conf import settings
from django.utils import timezone


def platform(request):
    tenant = getattr(request, "tenant", None)
    context = {
        "platform_name": settings.PLATFORM_NAME,
        "tenant": tenant,
        "tenant_source": getattr(request, "tenant_source", None),
    }
    if tenant is None:
        return context
    context["modules"] = tenant.enabled_modules()
    context["trial_days_left"] = tenant.trial_days_left(timezone.now().date())
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and not user.is_platform_admin:
        context["my_locations"] = list(user.allowed_locations())
    return context
