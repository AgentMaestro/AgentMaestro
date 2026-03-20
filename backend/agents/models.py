import ast
import json
import uuid
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, OperationalError
from django.utils.text import slugify

from agents.current import get_current_agent_creator
from core.models import Workspace, TimeStampedModel
from llm.services.model_failover import build_model_failover_candidates, normalize_backup_retry_policy


class Agent(TimeStampedModel):
    SLUG_MAX_LENGTH = 140

    DEFAULT_MODEL_ORDER = (
        "gpt-5.2",
        "gpt-5.2-2025-12-11",
        "gpt-5.2-chat-latest",
        "gpt-5.2-pro",
        "gpt-5.2-pro-2025-12-11",
        "gpt-5.1",
        "gpt-5.1-2025-11-13",
        "gpt-5.1-codex",
        "gpt-5.1-mini",
        "gpt-5.1-chat-latest",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-2025-08-07",
        "gpt-5-mini-2025-08-07",
        "gpt-5-nano-2025-08-07",
        "gpt-5-chat-latest",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    )
    DEFAULT_MODEL_CHOICES = tuple((model, model) for model in DEFAULT_MODEL_ORDER)
    DEFAULT_MODEL = "gpt-5"
    VALID_DEFAULT_MODELS = set(DEFAULT_MODEL_ORDER)
    DEFAULT_BACKUP_MODEL_API_BY_COMPANY = {
        "openai": "openai",
        "google": "gemini",
    }
    DEFAULT_BACKUP_RETRY_POLICY = {
        "retry_same_model_attempts": 1,
        "retryable_status_codes": [429, 502, 503, 504],
    }

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
    default_model = models.CharField(
        max_length=32,
        default=DEFAULT_MODEL,
    )
    temperature = models.DecimalField(max_digits=4, decimal_places=2, default=0.70)
    soul = models.TextField(blank=True, default="")
    policy_name = models.CharField(max_length=32, default="react")
    tool_policy_json = models.JSONField(default=dict, blank=True)
    sandbox_paths = models.JSONField(default=list, blank=True)
    backup_models_json = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Ordered fallback models as JSON, for example '
            '[{"company": "google", "api": "gemini", "name": "gemini-2.5-flash"}].'
        ),
    )
    backup_retry_policy_json = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Retry policy JSON, for example '
            '{"retry_same_model_attempts": 1, "retryable_status_codes": [429, 502, 503, 504]}.'
        ),
    )
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

    def get_sandbox_roots(self) -> tuple[Path, ...]:
        raw_paths = self._normalize_sandbox_paths(self.sandbox_paths)
        roots: list[Path] = []
        seen: set[Path] = set()
        for raw in raw_paths:
            if not raw:
                continue
            try:
                candidate = Path(str(raw)).expanduser()
            except Exception:
                continue
            if not candidate.is_absolute():
                candidate = Path(settings.BASE_DIR) / candidate
            try:
                normalized = candidate.resolve()
            except OSError:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            roots.append(normalized)
        return tuple(roots)

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
        self.default_model = self._normalize_default_model(self.default_model)
        self.sandbox_paths = self._normalize_sandbox_paths(self.sandbox_paths)
        self.backup_models_json = self._normalize_backup_models_json(self.backup_models_json)
        self.backup_retry_policy_json = self._normalize_backup_retry_policy_json(
            self.backup_retry_policy_json
        )
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

    @staticmethod
    def _normalize_sandbox_paths(raw: object | None) -> list[str]:
        def _expand_value(value: object | None) -> list[str]:
            if value is None:
                return []
            if isinstance(value, (list, tuple, set)):
                result: list[str] = []
                for member in value:
                    result.extend(_expand_value(member))
                return result
            if isinstance(value, dict):
                if "sandbox_paths" in value:
                    return _expand_value(value["sandbox_paths"])
                if "paths" in value:
                    return _expand_value(value["paths"])
                result: list[str] = []
                for member in value.values():
                    result.extend(_expand_value(member))
                return result
            if isinstance(value, str):
                candidate = value.strip()
                if not candidate:
                    return []
                if (candidate.startswith("{") and candidate.endswith("}")) or (
                    candidate.startswith("[") and candidate.endswith("]")
                ):
                    parsed = None
                    try:
                        parsed = json.loads(candidate)
                    except Exception:
                        try:
                            parsed = ast.literal_eval(candidate)
                        except Exception:
                            parsed = None
                    if parsed is not None:
                        return _expand_value(parsed)
                return [candidate]
            return [str(value)]

        candidates = _expand_value(raw)
        sanitized: list[str] = []
        for entry in candidates:
            candidate = str(entry).strip()
            if candidate:
                sanitized.append(candidate)
        return sanitized

    @classmethod
    def _normalize_backup_models_json(cls, raw: object | None) -> list[dict[str, str]]:
        def _coerce_entries(value: object | None) -> list[dict[str, object]]:
            if value is None:
                return []
            if isinstance(value, dict):
                return [value]
            if isinstance(value, (list, tuple, set)):
                entries: list[dict[str, object]] = []
                for member in value:
                    entries.extend(_coerce_entries(member))
                return entries
            if isinstance(value, str):
                candidate = value.strip()
                if not candidate:
                    return []
                if (candidate.startswith("{") and candidate.endswith("}")) or (
                    candidate.startswith("[") and candidate.endswith("]")
                ):
                    parsed = None
                    try:
                        parsed = json.loads(candidate)
                    except Exception:
                        try:
                            parsed = ast.literal_eval(candidate)
                        except Exception:
                            parsed = None
                    return _coerce_entries(parsed)
            return []

        candidates = _coerce_entries(raw)
        sanitized: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in candidates:
            company = str(entry.get("company") or "").strip().lower()
            name = str(entry.get("name") or "").strip()
            api = str(entry.get("api") or "").strip().lower()
            if not company or not name:
                continue
            if not api:
                api = cls.DEFAULT_BACKUP_MODEL_API_BY_COMPANY.get(company, "")
            candidate = {"company": company, "api": api, "name": name}
            key = (candidate["company"], candidate["api"], candidate["name"])
            if key in seen:
                continue
            seen.add(key)
            sanitized.append(candidate)
        return sanitized

    @classmethod
    def _normalize_backup_retry_policy_json(cls, raw: object | None) -> dict[str, object]:
        policy = normalize_backup_retry_policy(raw)
        if not policy:
            policy = dict(cls.DEFAULT_BACKUP_RETRY_POLICY)
        return policy

    def get_backup_models(self) -> list[dict[str, str]]:
        return self._normalize_backup_models_json(self.backup_models_json)

    def get_backup_retry_policy(self) -> dict[str, object]:
        return self._normalize_backup_retry_policy_json(self.backup_retry_policy_json)

    def get_model_failover_candidates(
        self,
        *,
        primary_provider: str | None = None,
        primary_model: str | None = None,
    ) -> list[dict[str, str]]:
        provider = primary_provider or settings.LLM_PROVIDER or "openai"
        model_name = primary_model or self.default_model
        return build_model_failover_candidates(
            primary_provider=provider,
            primary_model=model_name,
            backup_models=self.get_backup_models(),
            default_provider=provider,
        )

    @classmethod
    def _normalize_default_model(cls, value: object | None) -> str:
        candidate = (str(value or "")).strip()
        available = cls._get_available_model_set()
        if candidate and candidate in available:
            return candidate
        return cls.DEFAULT_MODEL

    @classmethod
    def get_default_model_choices(cls) -> list[tuple[str, str]]:
        names = cls._get_model_names_from_db()
        if names:
            return [(name, name) for name in names]
        return list(cls.DEFAULT_MODEL_CHOICES)

    @classmethod
    def _get_available_model_set(cls) -> set[str]:
        names = cls._get_model_names_from_db()
        if names:
            return set(names)
        return cls.VALID_DEFAULT_MODELS

    @classmethod
    def _get_model_names_from_db(cls) -> list[str]:
        try:
            ModelsAvailable = apps.get_model("llm", "ModelsAvailable")
        except LookupError:
            return []
        try:
            queryset = (
                ModelsAvailable.objects.filter(
                    models.Q(company__iexact="openai", api__iexact="responses")
                    | models.Q(company__iexact="google", api__iexact="gemini")
                )
                .order_by("name")
                .values_list("name", flat=True)
            )
        except OperationalError:
            return []
        except Exception:
            return []
        return [str(name) for name in queryset if name]

