from django.contrib import admin

from .models import ToolDefinition, ToolCall


admin.site.register(ToolDefinition)
admin.site.register(ToolCall)
