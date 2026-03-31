from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0015_agent_backup_models_json_and_backup_retry_policy_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="reasoning",
            field=models.CharField(
                choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
                default="medium",
                max_length=16,
            ),
        ),
    ]
