# Generated manually for the project_member webhook event flag

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0121_alter_estimate_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhook',
            name='project_member',
            field=models.BooleanField(default=False),
        ),
    ]
