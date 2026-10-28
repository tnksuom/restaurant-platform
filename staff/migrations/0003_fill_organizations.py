from django.db import migrations


def forwards(apps, schema_editor):
    Employee = apps.get_model("staff", "Employee")
    for emp in Employee.objects.select_related("restaurant").filter(organization__isnull=True, restaurant__isnull=False):
        emp.organization_id = emp.restaurant.organization_id
        emp.save(update_fields=["organization"])
        emp.locations.add(emp.restaurant_id)


class Migration(migrations.Migration):
    dependencies = [("staff", "0002_organizations_and_locations"), ("core", "0005_assign_organizations")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
