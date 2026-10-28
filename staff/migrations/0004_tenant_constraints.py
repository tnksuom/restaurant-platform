import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("staff", "0003_fill_organizations")]
    operations = [
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("login_identifier"),
                models.F("organization"),
                condition=models.Q(("organization__isnull", False)),
                name="employee_login_unique_in_org",
            ),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("login_identifier"),
                condition=models.Q(("organization__isnull", True)),
                name="platform_admin_login_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("is_platform_admin", True),
                        ("organization__isnull", True),
                        ("restaurant__isnull", True),
                    ),
                    models.Q(
                        ("is_platform_admin", False),
                        ("organization__isnull", False),
                        ("restaurant__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="employee_is_tenant_user_or_platform_admin",
            ),
        ),
    ]
