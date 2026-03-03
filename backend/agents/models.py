import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils.text import slugify

from agents.current import get_current_agent_creator
from core.models import Workspace, TimeStampedModel


class Agent(TimeStampedModel):
    SLUG_MAX_LENGTH = 140

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="workspace_agents",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="agents",
    )
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=SLUG_MAX_LENGTH, blank=True)
    description = models.TextField(blank=True, default="")
    default_model = models.CharField(max_length=80, default="gpt-5")
    temperature = models.DecimalField(max_digits=4, decimal_places=2, default=0.70)
    soul = models.TextField()
    policy_name = models.CharField(max_length=32, default="react")
    plan_enabled = models.BooleanField(default=False)
    tool_policy_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_agents",
    )
    default_conversation = models.ForeignKey(
        "control.ControlConversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="default_for_agents",
    )

    class Meta:
        indexes = [
            models.Index(fields=["workspace", "created_at"]),
            models.Index(fields=["owner", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["slug"], name="agents_agent_slug_unique")
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_name = getattr(self, "name", "")

    def __str__(self):
        return f"{self.workspace}:{self.name}"

    @classmethod
    def generate_unique_name(cls, base_name: str, exclude_pk=None) -> str:
        base = (base_name or "").strip()
        if not base:
            return base_name
        field = cls._meta.get_field("name")
        max_len = field.max_length or len(base)
        candidate = base[:max_len]
        suffix = 2
        while True:
            qs = cls.objects.filter(name=candidate)
            if exclude_pk is not None:
                qs = qs.exclude(pk=exclude_pk)
            if not qs.exists():
                return candidate
            suffix_value = f"-{suffix}"
            trim_len = max_len - len(suffix_value)
            candidate = f"{base[:max(trim_len, 0)]}{suffix_value}"
            suffix += 1

    def save(self, *args, **kwargs):
        self._ensure_owner()
        self._ensure_unique_name()
        if self._should_generate_slug():
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)
        self._original_name = self.name

    def _ensure_unique_name(self) -> None:
        if not self.name:
            return
        candidate = self.generate_unique_name(self.name, exclude_pk=self.pk)
        self.name = candidate

    def _ensure_owner(self) -> None:
        if self.owner_id:
            return
        creator = get_current_agent_creator()
        if creator and creator.is_authenticated:
            self.owner = creator
            return
        self.owner = self._get_default_owner()

    @staticmethod
    def _get_default_owner():
        User = get_user_model()
        user = User.objects.filter(is_active=True).order_by("id").first()
        if user:
            return user
        raise RuntimeError("No active user available to assign as Agent owner")

    def _should_generate_slug(self) -> bool:
        if not self.slug:
            return True
        if self.pk is None:
            return True
        return self.name != self._original_name

    def _build_unique_slug(self) -> str:
        base = slugify(self.name) or "agent"
        base = base[: self.SLUG_MAX_LENGTH]
        slug = base
        suffix = 1
        while True:
            conflict = Agent.objects.filter(slug=slug)
            if self.pk is not None:
                conflict = conflict.exclude(pk=self.pk)
            if not conflict.exists():
                return slug
            suffix_value = f"-{suffix}"
            trim_length = self.SLUG_MAX_LENGTH - len(suffix_value)
            slug = f"{base[:trim_length]}{suffix_value}"
            suffix += 1
