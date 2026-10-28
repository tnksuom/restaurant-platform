import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from staff.models import Employee, normalize_identifier

ENV_LOGIN = "INITIAL_ADMIN_LOGIN"
ENV_NAME = "INITIAL_ADMIN_NAME"
ENV_PASSWORD = "INITIAL_ADMIN_PASSWORD"


class Command(BaseCommand):
    help = (
        "Create the first platform administrator from environment variables, once, during deployment. "
        "Does nothing when a platform administrator already exists or the variables are not set, so it is "
        "safe to run on every deploy. Restaurants then sign up themselves or are created by the administrator."
    )

    def handle(self, *args, **options):
        if Employee.objects.filter(is_platform_admin=True, is_active=True).exists():
            if os.environ.get(ENV_PASSWORD):
                self.stdout.write(self.style.WARNING(
                    f"A platform administrator already exists. Remove {ENV_PASSWORD} from the hosting settings."
                ))
            else:
                self.stdout.write("A platform administrator already exists. Nothing to do.")
            return
        login = normalize_identifier(os.environ.get(ENV_LOGIN))
        password = os.environ.get(ENV_PASSWORD) or ""
        if not login or not password:
            self.stdout.write(self.style.WARNING(
                f"No platform administrator yet. Set {ENV_LOGIN} and {ENV_PASSWORD} (and optionally {ENV_NAME})."
            ))
            return
        name = (os.environ.get(ENV_NAME) or "").strip() or "Platform admin"
        try:
            validate_password(password, Employee(login_identifier=login, display_name=name))
        except ValidationError as exc:
            # Never echo the password itself into deploy logs.
            raise CommandError(f"{ENV_PASSWORD} is not strong enough: " + " ".join(exc.messages))
        Employee.objects.create_platform_admin(login, name, password)
        self.stdout.write(self.style.SUCCESS(
            f"Platform administrator '{login}' created. Sign in at /platform/login/ and then remove {ENV_PASSWORD}."
        ))
