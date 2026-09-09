from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0122_webhook_project_member"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="auto_archive_cancelled_issues",
            field=models.BooleanField(default=True),
        ),
    ]
