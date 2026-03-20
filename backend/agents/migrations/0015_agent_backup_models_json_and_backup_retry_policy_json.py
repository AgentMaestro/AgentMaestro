from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0014_remove_agent_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="backup_models_json",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Ordered fallback models as JSON, for example [{"company": "google", "api": "gemini", "name": "gemini-2.5-flash"}].',
            ),
        ),
        migrations.AddField(
            model_name="agent",
            name="backup_retry_policy_json",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Retry policy JSON, for example {"retry_same_model_attempts": 1, "retryable_status_codes": [429, 502, 503, 504]}.',
            ),
        ),
    ]
