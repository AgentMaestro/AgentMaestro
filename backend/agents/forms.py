from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.db.models.query import QuerySet

from core.models import Workspace
from llm.models import ModelsAvailable

from tools.models import ToolDefinition
from .models import Agent


class AgentBasicForm(forms.Form):
    name = forms.CharField(
        max_length=120,
        label="Agent Name",
        help_text="Give your agent a unique and memorable name.",
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Description",
    )
    soul = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Soul",
        help_text="Define how the agent should act, react, and behave.",
    )
    sandbox_paths = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Allowed paths",
        help_text="Supply newline-separated paths that the agent is allowed to access with tools.",
    )


class AgentLLMForm(forms.Form):
    default_model = forms.ChoiceField(
        choices=[],
        initial=Agent.DEFAULT_MODEL,
        label="Default Model",
        help_text="Select the default model for this agent.",
    )
    reasoning = forms.ChoiceField(
        choices=[],
        initial=Agent.DEFAULT_REASONING,
        label="Reasoning",
        help_text="Controls OpenAI reasoning.effort on the first request (low, medium, or high).",
    )
    backup_models = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Backup Models",
        help_text="Choose fallback models. They will fail over in the order shown on the page.",
    )
    temperature = forms.DecimalField(
        max_digits=4,
        decimal_places=2,
        min_value=Decimal("0.00"),
        max_value=Decimal("2.00"),
        initial=Decimal("0.70"),
        help_text="Set a value between 0.00 (deterministic) and 2.00 (creative).",
    )
    policy_name = forms.CharField(
        max_length=32,
        initial="react",
        label="Policy Name",
        help_text="Policy name determines orchestration behavior (e.g. react, planner).",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._refresh_model_choices()
        self._refresh_reasoning_choices()
        self._refresh_backup_model_choices()

    def _refresh_model_choices(self) -> None:
        choices = Agent.get_default_model_choices()
        self.fields["default_model"].choices = choices
        labels = [name for name, _ in choices]
        preview = ", ".join(labels[:12])
        if len(labels) > 12:
            preview += f", +{len(labels) - 12} more"
        self.fields["default_model"].help_text = (
            "Select the default model for this agent. "
            f"Available: {preview or Agent.DEFAULT_MODEL}."
        )

    def _refresh_reasoning_choices(self) -> None:
        choices = Agent.get_reasoning_choices()
        self.fields["reasoning"].choices = choices
        labels = [label for _, label in choices]
        preview = ", ".join(labels)
        self.fields["reasoning"].help_text = (
            "Controls OpenAI reasoning.effort on the first request. "
            f"Available: {preview or Agent.DEFAULT_REASONING}."
        )

    @staticmethod
    def _backup_model_value(model: ModelsAvailable) -> str:
        return f"{model.company}|{model.api}|{model.name}"

    @classmethod
    def _backup_model_label(cls, model: ModelsAvailable) -> str:
        return f"{model.company}:{model.api}:{model.name}"

    @classmethod
    def _decode_backup_model_value(cls, value: object) -> dict[str, str] | None:
        candidate = str(value or "").strip()
        if not candidate:
            return None
        parts = candidate.split("|", 2)
        if len(parts) != 3:
            return None
        company, api, name = (part.strip() for part in parts)
        if not company or not api or not name:
            return None
        return {"company": company, "api": api, "name": name}

    def _refresh_backup_model_choices(self) -> None:
        queryset = ModelsAvailable.objects.order_by("company", "api", "name")
        choices = [
            (self._backup_model_value(model), self._backup_model_label(model))
            for model in queryset
        ]
        self.fields["backup_models"].choices = choices
        initial_values = self.initial.get("backup_models")
        if isinstance(initial_values, list):
            normalized_initial: list[str] = []
            for entry in initial_values:
                if isinstance(entry, dict):
                    company = str(entry.get("company") or "").strip()
                    api = str(entry.get("api") or "").strip()
                    name = str(entry.get("name") or "").strip()
                    if company and api and name:
                        normalized_initial.append(f"{company}|{api}|{name}")
                else:
                    encoded = str(entry or "").strip()
                    if encoded:
                        normalized_initial.append(encoded)
            self.initial["backup_models"] = normalized_initial
        self.fields["backup_models"].help_text = (
            "Choose fallback models. They will fail over in the order shown on the page. "
            f"Available: {len(choices)}."
        )

class AgentToolsForm(forms.Form):
    def __init__(self, *args: Any, definitions: list[ToolDefinition], initial_tool_ids: list[str] | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.definitions = definitions
        initial_ids = {str(tool_id) for tool_id in (initial_tool_ids or [])}
        for definition in definitions:
            field_name = self._field_name(definition.tool_id)
            self.fields[field_name] = forms.BooleanField(
                required=False,
                label=definition.tool.name,
                help_text=definition.description or definition.tool.description,
            )
            if field_name not in self.initial:
                self.initial[field_name] = str(definition.tool_id) in initial_ids

    @staticmethod
    def _field_name(tool_id: object) -> str:
        return f"tool_{tool_id}"

    def get_selected_tool_ids(self) -> list[str]:
        return [
            str(definition.tool_id)
            for definition in self.definitions
            if self.cleaned_data.get(self._field_name(definition.tool_id))
        ]


class AgentWorkspaceForm(forms.Form):
    workspace = forms.ModelChoiceField(
        queryset=Workspace.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Choose an existing workspace",
    )
    workspace_name = forms.CharField(
        required=False,
        max_length=120,
        label="Or create a new workspace",
        help_text="If left blank, the selected workspace will be used or a default workspace will be created.",
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        ws = cleaned.get("workspace")
        name = cleaned.get("workspace_name")
        if not ws and not name:
            cleaned["workspace_name"] = "Default Workspace"
        return cleaned


class AgentOwnerForm(forms.Form):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        users = kwargs.pop("users", None)
        super().__init__(*args, **kwargs)
        queryset: QuerySet[Any] = (
            users if users is not None else get_user_model().objects.filter(is_active=True)
        )
        self.fields["owner"] = forms.ModelChoiceField(
            queryset=queryset.order_by("username"),
            required=False,
            label="Owner",
            help_text="Optional: reassign the agent owner (staff only).",
        )
