from django.db import migrations, models
import django.db.models.deletion
import uuid
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="LLMModelProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("agent_role", models.CharField(choices=[("planner", "Planner"), ("coder", "Coder"), ("generic", "Generic")], default="generic", max_length=20)),
                ("provider", models.CharField(default="openai", max_length=50)),
                ("model", models.CharField(max_length=200)),
                ("reasoning_model", models.CharField(blank=True, max_length=200, null=True)),
                ("temperature", models.FloatField(blank=True, null=True)),
                ("max_output_tokens", models.IntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("extra", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="LLMRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("provider", models.CharField(max_length=50)),
                ("model", models.CharField(max_length=200)),
                ("orchestration_run_id", models.UUIDField(blank=True, null=True)),
                ("agent_name", models.CharField(blank=True, max_length=100)),
                ("purpose", models.CharField(blank=True, max_length=255)),
                ("status", models.CharField(choices=[("started", "Started"), ("completed", "Completed"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="started", max_length=20)),
                ("token_prompt", models.IntegerField(blank=True, null=True)),
                ("token_completion", models.IntegerField(blank=True, null=True)),
                ("token_total", models.IntegerField(blank=True, null=True)),
                ("provider_meta", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True)),
                ("profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="runs", to="llm.llmmodelprofile")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LLMToolCall",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("tool_name", models.CharField(max_length=200)),
                ("arguments", models.JSONField(blank=True, default=dict)),
                ("result", models.JSONField(blank=True, null=True)),
                ("success", models.BooleanField(default=False)),
                ("error", models.TextField(blank=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tool_calls", to="llm.llmrun")),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="LLMMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("role", models.CharField(choices=[("system", "System"), ("user", "User"), ("assistant", "Assistant"), ("tool", "Tool")], max_length=20)),
                ("content", models.TextField(blank=True)),
                ("name", models.CharField(blank=True, max_length=100)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="llm.llmrun")),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
