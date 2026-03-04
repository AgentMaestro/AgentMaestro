from django.contrib import admin

from .models import Agent


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "workspace", "role", "default_model", "soul", "owner")
    search_fields = ("id", "name", "slug")
    list_filter = ("workspace", "owner")
