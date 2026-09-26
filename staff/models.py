from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from core.models import Restaurant


class Role(models.TextChoices):
    OWNER = "OWNER", _("Owner")
    MANAGER = "MANAGER", _("Manager")
    EMPLOYEE = "EMPLOYEE", _("Employee")


def normalize_identifier(value):
    return (value or "").strip().lower()


class EmployeeManager(BaseUserManager):
    def get_by_natural_key(self, username):
        return self.get(login_identifier=normalize_identifier(username))

    def create_user(self, restaurant, login_identifier, display_name, role=Role.EMPLOYEE, password=None, **extra):
        employee = self.model(
            restaurant=restaurant,
            login_identifier=normalize_identifier(login_identifier),
            display_name=display_name,
            role=role,
            **extra,
        )
        if password:
            employee.set_password(password)
        else:
            employee.set_unusable_password()
        employee.full_clean(exclude=["password"])
        employee.save()
        return employee


class Employee(AbstractBaseUser):
    """A staff member and their login identity (Django's authentication user).

    The authentication identity and the staff profile are the same row, so the
    auth user ID *is* the employee ID used by timesheets, stock movements and
    checklist records. Records are deactivated, never deleted.
    """

    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="employees")
    display_name = models.CharField(_("display name"), max_length=150)
    login_identifier = models.CharField(
        _("login name or email"),
        max_length=150,
        unique=True,
        help_text=_("Used to sign in. Not case-sensitive."),
    )
    phone = models.CharField(_("contact number"), max_length=40, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.EMPLOYEE)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "login_identifier"
    REQUIRED_FIELDS = ["display_name"]

    objects = EmployeeManager()

    class Meta:
        ordering = ["display_name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("login_identifier"), "restaurant", name="employee_login_ci_unique"),
            models.CheckConstraint(
                condition=models.Q(login_identifier=Lower("login_identifier")),
                name="employee_login_lowercase",
            ),
            models.CheckConstraint(condition=models.Q(role__in=Role.values), name="employee_role_valid"),
        ]

    def __str__(self):
        return self.display_name

    def clean(self):
        super().clean()
        self.login_identifier = normalize_identifier(self.login_identifier)

    @property
    def is_owner(self):
        return self.role == Role.OWNER

    @property
    def is_management(self):
        return self.role in (Role.OWNER, Role.MANAGER)

    @property
    def has_credentials(self):
        return self.has_usable_password()
