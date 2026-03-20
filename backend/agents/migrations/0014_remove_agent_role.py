from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0013_alter_agent_default_model"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="agent",
            name="role",
        ),
    ]
