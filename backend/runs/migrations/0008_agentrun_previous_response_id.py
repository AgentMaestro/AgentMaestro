from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0007_rename_runs_runarchive_run_created_at_idx_runs_runarc_run_id_687157_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrun",
            name="previous_response_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
