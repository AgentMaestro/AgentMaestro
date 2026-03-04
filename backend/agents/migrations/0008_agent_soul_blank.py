from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0007_agent_sandbox_paths"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agent",
            name="soul",
            field=models.TextField(blank=True, default=""),
        ),
    ]
