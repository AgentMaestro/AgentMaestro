# backend/runs/tasks.py
from __future__ import annotations

from agentmaestro.celery import app

from runs.services.ticker import run_tick as run_tick_service


@app.task(name="runs.tasks.run_tick")
def run_tick(run_id: str):
    """Celery entry point for advancing a run via the tick service."""
    return run_tick_service(run_id=run_id)
