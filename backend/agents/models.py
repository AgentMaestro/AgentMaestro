import uuid

from django.conf import settings
from django.db import models

from core.models import Workspace, TimeStampedModel


class Agent(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agents",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    default_model = models.CharField(max_length=80, default="gpt-5")
    temperature = models.DecimalField(max_digits=4, decimal_places=2, default=0.70)
    system_prompt = models.TextField()
    plan_enabled = models.BooleanField(default=False)
    tool_policy_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_agents",
    )

    class Meta:
        unique_together = [("workspace", "name")]
        indexes = [
            models.Index(fields=["workspace", "name"]),
            models.Index(fields=["workspace", "created_at"]),
        ]

    def __str__(self):
        return f"{self.workspace}:{self.name}"
