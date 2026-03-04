from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0010_remove_plan_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="role",
            field=models.CharField(default="assisting", max_length=32),
        ),
    ]
