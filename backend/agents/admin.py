from django import forms
from django.contrib import admin

from .models import Agent


class AgentAdminForm(forms.ModelForm):
    default_model = forms.ChoiceField(label="Default Model")

    class Meta:
        model = Agent
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Agent.get_default_model_choices()
        self.fields["default_model"].choices = choices


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "workspace",
        "role",
        "default_model",
        "formatted_sandbox_paths",
        "soul",
        "owner",
    )
    search_fields = ("id", "name", "slug")
    list_filter = ("workspace", "owner")
    form = AgentAdminForm

    def formatted_sandbox_paths(self, obj: Agent) -> str:
        paths = Agent._normalize_sandbox_paths(getattr(obj, "sandbox_paths", None))
        return ", ".join(paths)
    formatted_sandbox_paths.short_description = "Sandbox"
