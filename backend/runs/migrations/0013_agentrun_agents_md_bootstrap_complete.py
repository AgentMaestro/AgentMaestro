from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0012_alter_subrunlink_failure_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrun",
            name="agents_md_bootstrap_complete",
            field=models.BooleanField(default=False),
        ),
    ]
