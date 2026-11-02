import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("db", "0123_project_auto_archive_cancelled_issues")]
    operations = [
        migrations.CreateModel(
            name="NexusMembershipCleanup",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("request", models.JSONField()),
                ("result", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("workspace", models.ForeignKey(to="db.workspace", on_delete=django.db.models.deletion.RESTRICT)),
                ("member", models.ForeignKey(to="db.user", on_delete=django.db.models.deletion.RESTRICT)),
            ],
            options={"db_table": "nexus_membership_cleanups"},
        )
    ]
