import uuid

from django.conf import settings
from django.db import migrations, models
from django.db.models import deletion
from django.utils.text import slugify


def _ensure_owner_and_slug(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app_label, user_model_name)
    scott, _ = User.objects.get_or_create(username="scott", defaults={"is_active": True})

    slug_field = Agent._meta.get_field("slug")
    max_slug_length = slug_field.max_length or 140
    used_names = set()
    used_slugs = set()

    for agent in Agent.objects.all().order_by("created_at", "id"):
        base_name = (agent.name or "agent").strip() or "agent"
        candidate_name = base_name
        suffix = 1
        while candidate_name.lower() in used_names:
            candidate_name = f"{base_name}-{suffix}"
            suffix += 1
        used_names.add(candidate_name.lower())
        if agent.name != candidate_name:
            agent.name = candidate_name

        base_slug = slugify(agent.name) or f"agent-{uuid.uuid4().hex[:8]}"
        base_slug = base_slug[:max_slug_length]
        candidate_slug = base_slug
        suffix = 1
        while candidate_slug in used_slugs:
            suffix_token = f"-{suffix}"
            trim_limit = max_slug_length - len(suffix_token)
            candidate_slug = f"{base_slug[:trim_limit]}{suffix_token}"
            suffix += 1
        used_slugs.add(candidate_slug)
        agent.slug = candidate_slug

        agent.owner = scott
        agent.save(update_fields=["name", "slug", "owner"])


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0004_agent_soul"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="slug",
            field=models.SlugField(
                blank=True,
                default="",
                max_length=140,
            ),
        ),
        migrations.AddField(
            model_name="agent",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=deletion.PROTECT,
                related_name="agents",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="agent",
            name="workspace",
            field=models.ForeignKey(
                on_delete=deletion.CASCADE,
                related_name="workspace_agents",
                to="core.workspace",
            ),
        ),
        migrations.RunPython(_ensure_owner_and_slug, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="agent",
            name="owner",
            field=models.ForeignKey(
                on_delete=deletion.PROTECT,
                related_name="agents",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="agent",
            name="slug",
            field=models.SlugField(
                blank=True,
                max_length=140,
            ),
        ),
        migrations.AlterField(
            model_name="agent",
            name="name",
            field=models.CharField(
                max_length=120,
                unique=True,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="agent",
            unique_together=set(),
        ),
        migrations.RemoveIndex(
            model_name="agent",
            name="agents_agen_workspa_d55b1d_idx",
        ),
        migrations.AddIndex(
            model_name="agent",
            index=models.Index(
                fields=["owner", "created_at"],
                name="agents_agent_owner_created_idx",
            ),
        ),
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS agents_agent_slug_0135d8cf_like;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="agent",
            constraint=models.UniqueConstraint(fields=["slug"], name="agents_agent_slug_unique"),
        ),
    ]
