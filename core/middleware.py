from django.utils import timezone


class RestaurantTimezoneMiddleware:
    """Display all datetimes in the signed-in user's restaurant time zone."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.restaurant_id:
            timezone.activate(user.restaurant.tz)
        else:
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()


class HealthCheckMiddleware:
    """Answer /healthz before host validation or HTTPS redirects run.

    Hosting platforms call the health check from inside their network, sometimes
    with an internal IP as the Host header, which Django would otherwise reject.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info == "/healthz":
            from .views import healthz

            return healthz(request)
        return self.get_response(request)
