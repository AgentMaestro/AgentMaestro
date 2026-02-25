# backend/runs/services/steps.py
from __future__ import annotations

from typing import Any, Dict, Optional

from django.db import transaction

from runs.models import AgentRun, AgentStep


@transaction.atomic
def append_step(
    run_id: str,
    *,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> AgentStep:
    """
    Append an AgentStep to a run with an atomic, sequential step_index.

    The run row is locked so concurrent callers cannot allocate the same index.
    """
    run = AgentRun.objects.select_for_update().get(id=run_id)

    next_index = run.current_step_index + 1
    resolved_correlation = correlation_id or run.correlation_id

    step = AgentStep.objects.create(
        run=run,
        step_index=next_index,
        kind=kind,
        payload=payload or {},
        correlation_id=resolved_correlation,
    )

    run.current_step_index = next_index
    run.save(update_fields=["current_step_index", "updated_at"])

    return step
