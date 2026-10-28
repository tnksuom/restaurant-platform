from django.db import migrations

from core.db import append_only_trigger_sql, drop_append_only_trigger_sql


def create_plans(apps, schema_editor):
    from tenants.plans import ensure_default_plans

    ensure_default_plans(apps.get_model("tenants", "Plan"))


class Migration(migrations.Migration):
    dependencies = [("tenants", "0001_initial"), ("core", "0003_append_only_triggers")]
    operations = [
        migrations.RunPython(create_plans, migrations.RunPython.noop),
        migrations.RunSQL(append_only_trigger_sql("tenants_organizationevent"),
                          drop_append_only_trigger_sql("tenants_organizationevent")),
    ]
