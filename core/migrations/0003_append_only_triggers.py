from django.db import migrations

from core.db import DROP_FUNCTION_SQL, FUNCTION_SQL, append_only_trigger_sql, drop_append_only_trigger_sql


class Migration(migrations.Migration):
    dependencies = [("core", "0002_initial")]

    operations = [
        migrations.RunSQL(FUNCTION_SQL, DROP_FUNCTION_SQL),
        migrations.RunSQL(append_only_trigger_sql("core_auditevent"), drop_append_only_trigger_sql("core_auditevent")),
    ]
