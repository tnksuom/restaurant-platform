from django.contrib.auth.backends import ModelBackend

from .models import normalize_identifier


class EmployeeBackend(ModelBackend):
    """Case-insensitive login; inactive staff can neither log in nor keep a session.

    ModelBackend.get_user() re-reads the employee on every request and returns
    None for inactive accounts, so deactivation and role changes apply to
    already signed-in sessions on their next request.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        return super().authenticate(request, username=normalize_identifier(username), password=password, **kwargs)
