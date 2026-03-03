from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0003_agent_default_conversation"),
    ]

    operations = [
        migrations.RenameField(
            model_name="agent",
            old_name="system_prompt",
            new_name="soul",
        ),
    ]
