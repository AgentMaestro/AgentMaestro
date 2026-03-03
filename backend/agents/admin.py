from django.contrib import admin

from .models import Agent


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("id", "workspace", "slug", "name", "owner", "soul")
    search_fields = ("id", "name", "slug")
    list_filter = ("workspace", "owner")
