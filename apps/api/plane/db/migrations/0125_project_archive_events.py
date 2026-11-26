import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("db", "0124_nexus_membership_cleanup")]

    operations = [
        migrations.CreateModel(
            name="ProjectArchiveEvent",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("archived", models.BooleanField()),
                ("actor_id", models.UUIDField(null=True)),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("project", models.ForeignKey(to="db.project", on_delete=django.db.models.deletion.CASCADE, related_name="archive_events")),
                ("workspace", models.ForeignKey(to="db.workspace", on_delete=django.db.models.deletion.CASCADE)),
            ],
            options={
                "db_table": "project_archive_events",
                "ordering": ("occurred_at", "id"),
                "indexes": [models.Index(fields=["project", "occurred_at"], name="project_archive_event_time_idx")],
            },
        ),
    ]
