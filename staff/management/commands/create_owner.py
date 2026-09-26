import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import audit
from core.models import Restaurant
from staff.models import Employee, Role, normalize_identifier


class Command(BaseCommand):
    help = (
        "Provision the first Owner account (and the restaurant, if none exists). "
        "This is the only way to create an account without signing in; there is no public sign-up page."
    )

    def add_arguments(self, parser):
        parser.add_argument("--login", required=True, help="Login name or email for the owner")
        parser.add_argument("--name", required=True, help="Display name")
        parser.add_argument("--restaurant", default="My Restaurant", help="Restaurant name if creating it")
        parser.add_argument("--time-zone", default=None, help="IANA time zone, default from RESTAURANT_TIME_ZONE")
        parser.add_argument(
            "--password-from-env",
            action="store_true",
            help="Read the password from OWNER_PASSWORD instead of prompting (for automated setup)",
        )
        parser.add_argument(
            "--allow-additional",
            action="store_true",
            help="Allow creating another owner even though an active owner already exists",
        )

    def handle(self, *args, **opts):
        login = normalize_identifier(opts["login"])
        if Employee.objects.filter(login_identifier=login).exists():
            raise CommandError("That login is already in use.")
        if Employee.objects.filter(role=Role.OWNER, is_active=True).exists() and not opts["allow_additional"]:
            raise CommandError(
                "An active owner already exists. Owners can add staff in the app. "
                "Use --allow-additional only for recovery."
            )
        if opts["password_from_env"]:
            password = os.environ.get("OWNER_PASSWORD", "")
        else:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords did not match.")

        with transaction.atomic():
            restaurant = Restaurant.objects.order_by("pk").first()
            if restaurant is None:
                restaurant = Restaurant(name=opts["restaurant"])
                if opts["time_zone"]:
                    restaurant.time_zone = opts["time_zone"]
                restaurant.full_clean()
                restaurant.save()
            owner = Employee(restaurant=restaurant, login_identifier=login, display_name=opts["name"], role=Role.OWNER)
            try:
                validate_password(password, owner)
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages))
            owner.set_password(password)
            owner.full_clean()
            owner.save()
            audit.record(None, "employee.owner_provisioned", owner, {"via": "create_owner command"})
        self.stdout.write(self.style.SUCCESS(f"Owner '{login}' created for {restaurant.name}."))
