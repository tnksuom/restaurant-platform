from .permissions import is_manager


def navigation(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    return {"is_manager": is_manager(user), "restaurant": user.restaurant}
