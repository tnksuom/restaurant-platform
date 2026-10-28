import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_assign_organizations")]
    operations = [
        migrations.AlterField(
            model_name="restaurant",
            name="organization",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurants",
                                    to="tenants.organization"),
        ),
    ]
