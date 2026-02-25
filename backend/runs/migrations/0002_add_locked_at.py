from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrun",
            name="locked_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
