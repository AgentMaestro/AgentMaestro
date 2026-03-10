from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from tools.models import ToolCall
from tools.services.execution import execute_tool_call

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="tools.execute_tool_call_async", max_retries=2)
def execute_tool_call_async(self, tool_call_id: str) -> None:
    logger.info("Celery execute_tool_call_async start tool_call_id=%s task_id=%s", tool_call_id, self.request.id)
    tool_call = ToolCall.objects.filter(id=tool_call_id).first()
    if not tool_call:
        retry_count = int(getattr(self.request, "retries", 0) or 0)
        if retry_count < 2:
            next_retry = retry_count + 1
            logger.warning(
                "Celery tool_call %s missing, retrying in 250ms retry=%s/2",
                tool_call_id,
                next_retry,
            )
            raise self.retry(countdown=0.25)
        logger.warning("Celery tool_call %s missing, aborting after retries=2", tool_call_id)
        return

    tool_call.celery_task_id = self.request.id or ""
    tool_call.save(update_fields=["celery_task_id", "updated_at"])

    try:
        logger.info("Celery invoking execute_tool_call for tool_call=%s run=%s", tool_call.id, tool_call.run_id)
        execute_tool_call(str(tool_call.id))
        logger.info("Celery execute_tool_call finished tool_call=%s", tool_call.id)
    except Exception as exc:  # pragma: no cover
        logger.exception("Celery execute_tool_call raised for %s", tool_call_id)
        tool_call.status = ToolCall.Status.FAILED
        tool_call.error = str(exc)
        tool_call.observed_at = timezone.now()
        tool_call.save(update_fields=["status", "error", "observed_at", "updated_at"])
