from django.db import migrations, models


def _backfill_agent_workspaces(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    through = Agent._meta.get_field("workspaces").remote_field.through
    rows = []
    for agent in Agent.objects.exclude(workspace_id__isnull=True).only("id", "workspace_id"):
        rows.append(
            through(
                agent_id=agent.id,
                workspace_id=agent.workspace_id,
            )
        )
    if rows:
        through.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0016_agent_reasoning"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="workspaces",
            field=models.ManyToManyField(
                blank=True,
                related_name="accessible_agents",
                to="core.workspace",
            ),
        ),
        migrations.RunPython(_backfill_agent_workspaces, migrations.RunPython.noop),
    ]
