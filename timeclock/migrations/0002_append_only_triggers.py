from django.db import migrations

from core.db import append_only_trigger_sql, drop_append_only_trigger_sql


class Migration(migrations.Migration):
    dependencies = [("timeclock", "0001_initial"), ("core", "0003_append_only_triggers")]

    operations = [
        migrations.RunSQL(append_only_trigger_sql(t), drop_append_only_trigger_sql(t))
        for t in ("timeclock_shiftevent", "timeclock_timecorrection")
    ]
