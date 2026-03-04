from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0009_merge_0008_agent_soul_blank_0008_alter_agent_soul"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="agent",
            name="plan_enabled",
        ),
    ]
