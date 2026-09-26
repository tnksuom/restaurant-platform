from django.utils import timezone


class RestaurantTimezoneMiddleware:
    """Display all datetimes in the signed-in user's restaurant time zone."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            timezone.activate(user.restaurant.tz)
        else:
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
