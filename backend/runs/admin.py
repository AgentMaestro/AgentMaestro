from django.contrib import admin

from .models import AgentRun, AgentStep, RunEvent, Artifact


admin.site.register(AgentRun)
admin.site.register(AgentStep)
admin.site.register(RunEvent)
admin.site.register(Artifact)
