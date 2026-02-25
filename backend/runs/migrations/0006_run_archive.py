import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0005_agentrun_correlation_id_agentstep_correlation_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrun",
            name="archived_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="RunArchive",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ("archive_path", models.TextField()),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="archives",
                        to="runs.agentrun",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["run", "created_at"],
                        name="runs_runarchive_run_created_at_idx",
                    ),
                ],
            },
        ),
    ]
