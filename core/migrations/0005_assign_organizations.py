from django.db import migrations


def forwards(apps, schema_editor):
    """Locations created before the platform existed join one organization called "default"."""
    Restaurant = apps.get_model("core", "Restaurant")
    if not Restaurant.objects.filter(organization__isnull=True).exists():
        return
    Organization = apps.get_model("tenants", "Organization")
    Plan = apps.get_model("tenants", "Plan")
    from tenants.plans import ensure_default_plans

    ensure_default_plans(Plan)
    first = Restaurant.objects.order_by("pk").first()
    org, _ = Organization.objects.get_or_create(
        slug="default",
        defaults={"name": first.name, "plan": Plan.objects.get(code="business"), "status": "ACTIVE",
                  "module_menu": True, "module_website": True},
    )
    Restaurant.objects.filter(organization__isnull=True).update(organization=org)


class Migration(migrations.Migration):
    dependencies = [("core", "0004_restaurant_organization"), ("tenants", "0002_plans_and_history")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
