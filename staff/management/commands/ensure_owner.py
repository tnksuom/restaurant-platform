import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import audit
from core.models import Restaurant
from staff.models import Employee, Role, normalize_identifier

ENV_LOGIN = "INITIAL_OWNER_LOGIN"
ENV_NAME = "INITIAL_OWNER_NAME"
ENV_PASSWORD = "INITIAL_OWNER_PASSWORD"
ENV_RESTAURANT = "INITIAL_RESTAURANT_NAME"


class Command(BaseCommand):
    help = (
        "Create the first Owner from environment variables, once, during deployment. "
        "Does nothing when an active owner already exists or the variables are not set, "
        "so it is safe to run on every deploy. There is still no public sign-up page."
    )

    def handle(self, *args, **options):
        if Employee.objects.filter(role=Role.OWNER, is_active=True).exists():
            if os.environ.get(ENV_PASSWORD):
                self.stdout.write(
                    self.style.WARNING(
                        f"An owner already exists. Remove {ENV_PASSWORD} from the hosting settings; it is no longer used."
                    )
                )
            else:
                self.stdout.write("An owner already exists. Nothing to do.")
            return
        login = normalize_identifier(os.environ.get(ENV_LOGIN, ""))
        password = os.environ.get(ENV_PASSWORD, "")
        name = (os.environ.get(ENV_NAME) or "").strip() or "Owner"
        if not login or not password:
            self.stdout.write(
                self.style.WARNING(
                    f"No owner yet. Set {ENV_LOGIN} and {ENV_PASSWORD} (and optionally {ENV_NAME}, "
                    f"{ENV_RESTAURANT}) and deploy again."
                )
            )
            return
        with transaction.atomic():
            restaurant = Restaurant.objects.order_by("pk").first()
            if restaurant is None:
                restaurant = Restaurant(name=(os.environ.get(ENV_RESTAURANT) or "").strip() or "My Restaurant")
                restaurant.full_clean()
                restaurant.save()
            if Employee.objects.filter(login_identifier=login).exists():
                raise CommandError(f"The login '{login}' is already taken by a non-owner account.")
            owner = Employee(restaurant=restaurant, login_identifier=login, display_name=name, role=Role.OWNER)
            try:
                validate_password(password, owner)
            except ValidationError as exc:
                # Never print the password itself.
                raise CommandError(f"{ENV_PASSWORD} is not strong enough: " + " ".join(exc.messages))
            owner.set_password(password)
            owner.full_clean()
            owner.save()
            audit.record(None, "employee.owner_provisioned", owner, {"via": "ensure_owner (deployment)"})
        self.stdout.write(
            self.style.SUCCESS(
                f"Owner '{login}' created for {restaurant.name}. Now remove {ENV_PASSWORD} from the hosting settings."
            )
        )
