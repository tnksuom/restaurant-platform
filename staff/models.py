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
        """Natural keys are only unique for platform administrators (organization-less accounts)."""
        return self.get(login_identifier=normalize_identifier(username), organization__isnull=True)

    def working_at(self, restaurant):
        """Staff of a location: everyone assigned to it, plus the organization's owners."""
        return self.filter(organization_id=restaurant.organization_id).filter(
            models.Q(locations=restaurant) | models.Q(role=Role.OWNER)
        ).distinct()

    def create_platform_admin(self, login_identifier, display_name, password):
        admin = self.model(
            organization=None, restaurant=None, is_platform_admin=True, role=Role.OWNER,
            login_identifier=normalize_identifier(login_identifier), display_name=display_name,
        )
        admin.set_password(password)
        admin.full_clean(exclude=["password"])
        admin.save()
        return admin

    def create_user(self, restaurant, login_identifier, display_name, role=Role.EMPLOYEE, password=None, **extra):
        employee = self.model(
            restaurant=restaurant,
            organization_id=restaurant.organization_id,
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
        employee.locations.add(restaurant)
        return employee


class Employee(AbstractBaseUser):
    """A staff member and their login identity (Django's authentication user).

    The authentication identity and the staff profile are the same row, so the
    auth user ID *is* the employee ID used by timesheets, stock movements and
    checklist records. Records are deactivated, never deleted.
    """

    organization = models.ForeignKey(
        "tenants.Organization", on_delete=models.PROTECT, null=True, blank=True, related_name="employees"
    )
    # The location this person is working in right now; every screen shows that location's data.
    # Empty only for platform administrators.
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.PROTECT, null=True, blank=True, related_name="current_employees"
    )
    # Locations this person works at. Owners may open every location of their organization.
    locations = models.ManyToManyField(Restaurant, blank=True, related_name="employees")
    is_platform_admin = models.BooleanField(default=False)
    display_name = models.CharField(_("display name"), max_length=150)
    login_identifier = models.CharField(
        _("login name or email"),
        max_length=150,
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
            # Login names are unique within an organization; platform admins are unique among themselves.
            models.UniqueConstraint(
                Lower("login_identifier"), "organization", name="employee_login_unique_in_org",
                condition=models.Q(organization__isnull=False),
            ),
            models.UniqueConstraint(
                Lower("login_identifier"), name="platform_admin_login_unique",
                condition=models.Q(organization__isnull=True),
            ),
            models.CheckConstraint(
                condition=models.Q(is_platform_admin=True, organization__isnull=True, restaurant__isnull=True)
                | models.Q(is_platform_admin=False, organization__isnull=False, restaurant__isnull=False),
                name="employee_is_tenant_user_or_platform_admin",
            ),
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

    def allowed_locations(self):
        if self.is_platform_admin:
            return self.locations.none()
        if self.role == Role.OWNER:
            return self.organization.restaurants.order_by("name", "pk")
        return self.locations.filter(organization_id=self.organization_id).order_by("name", "pk")

    def works_at(self, restaurant):
        if self.is_platform_admin or restaurant.organization_id != self.organization_id:
            return False
        return self.role == Role.OWNER or self.locations.filter(pk=restaurant.pk).exists()

    @property
    def has_credentials(self):
        return self.has_usable_password()
