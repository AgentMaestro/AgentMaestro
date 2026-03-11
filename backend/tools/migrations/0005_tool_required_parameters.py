from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tools", "0004_alter_agenttoolgrant_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tool",
            name="required_parameters",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
