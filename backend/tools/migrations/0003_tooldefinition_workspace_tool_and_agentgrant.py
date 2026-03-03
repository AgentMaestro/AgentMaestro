from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


def assign_tool_to_definition(apps, schema_editor):
    ToolDefinition = apps.get_model("tools", "ToolDefinition")
    Tool = apps.get_model("tools", "Tool")
    for definition in ToolDefinition.objects.all():
        if definition.tool_id or not definition.name:
            continue
        tool = Tool.objects.filter(name=definition.name).first()
        if tool:
            definition.tool = tool
            definition.save(update_fields=["tool"])


class Migration(migrations.Migration):

    dependencies = [
        ("tools", "0002_toolcall_correlation_id"),
        (
            "agents",
            "0006_rename_agents_agent_owner_created_idx_agents_agen_owner_i_1312f7_idx",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name='ToolGroup',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('name', models.CharField(max_length=128, unique=True)),
                ('description', models.TextField(blank=True, default='')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Tool',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('name', models.CharField(max_length=80, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=120, unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('risk', models.CharField(max_length=12, choices=[('SAFE', 'Safe'), ('ELEVATED', 'Elevated'), ('DANGEROUS', 'Dangerous')], default='SAFE')),
                ('args_schema', models.JSONField(blank=True, default=dict)),
                ('requires_approval', models.BooleanField(default=False)),
                ('released', models.BooleanField(default=True)),
                ('tool_group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tools', to='tools.ToolGroup')),
            ],
            options={
                'ordering': ['tool_group__name', '-risk', 'name'],
            },
        ),

        migrations.AddField(
            model_name="tooldefinition",
            name="tool",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspace_definitions",
                to="tools.Tool",
            ),
        ),
        migrations.AddField(
            model_name="tooldefinition",
            name="config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterUniqueTogether(
            name="tooldefinition",
            unique_together=set(),
        ),
        migrations.AddIndex(
            model_name="tooldefinition",
            index=models.Index(
                fields=["workspace", "tool"],
                name="tools_tooldefinition_workspace_tool_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tooldefinition",
            index=models.Index(
                fields=["workspace", "enabled"],
                name="tools_tooldefinition_workspace_enabled_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="tooldefinition",
            constraint=models.UniqueConstraint(
                fields=["workspace", "tool"],
                name="tools_tooldefinition_workspace_tool_uniq",
            ),
        ),
        migrations.RunPython(assign_tool_to_definition, reverse_code=migrations.RunPython.noop),
        migrations.CreateModel(
            name="AgentToolGrant",
            fields=[
                (
                    "id",
                    models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, db_index=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "enabled",
                    models.BooleanField(default=False),
                ),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tool_grants",
                        to="agents.Agent",
                    ),
                ),
                (
                    "tool",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_grants",
                        to="tools.Tool",
                    ),
                ),
            ],
            options={
                "ordering": (),
            },
        ),
        migrations.AlterUniqueTogether(
            name="agenttoolgrant",
            unique_together={("agent", "tool")},
        ),
        migrations.AddIndex(
            model_name="agenttoolgrant",
            index=models.Index(fields=["agent", "tool"], name="tools_agenttoolgrant_agent_tool_idx"),
        ),
    ]
