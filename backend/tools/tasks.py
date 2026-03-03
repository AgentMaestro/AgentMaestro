from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from tools.models import ToolCall
from tools.services.execution import execute_tool_call


@shared_task(bind=True, name="tools.execute_tool_call_async")
def execute_tool_call_async(self, tool_call_id: str) -> None:
    tool_call = ToolCall.objects.filter(id=tool_call_id).first()
    if not tool_call:
        return

    tool_call.celery_task_id = self.request.id or ""
    tool_call.save(update_fields=["celery_task_id", "updated_at"])

    try:
        execute_tool_call(str(tool_call.id))
    except Exception as exc:  # pragma: no cover
        tool_call.status = ToolCall.Status.FAILED
        tool_call.error = str(exc)
        tool_call.observed_at = timezone.now()
        tool_call.save(update_fields=["status", "error", "observed_at", "updated_at"])
