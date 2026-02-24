from django.contrib import admin

from .models import Workspace, WorkspaceMembership


admin.site.register(Workspace)
admin.site.register(WorkspaceMembership)
