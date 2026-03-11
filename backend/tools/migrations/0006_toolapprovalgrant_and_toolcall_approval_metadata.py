from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tools", "0005_tool_required_parameters"),
    ]

    operations = [
        migrations.CreateModel(
            name="ToolApprovalGrant",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tool_name", models.CharField(max_length=80)),
                ("scope_type", models.CharField(choices=[("EXACT_PATH", "Exact Path"), ("PATH_PREFIX", "Path Prefix"), ("REPO_EXACT", "Repository")], max_length=16)),
                ("scope_path", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_tool_approval_grants", to=settings.AUTH_USER_MODEL)),
                ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="revoked_tool_approval_grants", to=settings.AUTH_USER_MODEL)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tool_approval_grants", to="runs.agentrun")),
                ("source_tool_call", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="spawned_approval_grants", to="tools.toolcall")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tool_approval_grants", to="core.workspace")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["run", "tool_name", "revoked_at"], name="tools_toola_run_id_97a44f_idx"),
                    models.Index(fields=["workspace", "run", "revoked_at"], name="tools_toola_workspa_79c499_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="toolcall",
            name="approval_grant",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_tool_calls", to="tools.toolapprovalgrant"),
        ),
        migrations.AddField(
            model_name="toolcall",
            name="approval_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
