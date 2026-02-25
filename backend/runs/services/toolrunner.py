from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def signal_toolrunner_cancel(run_id: str) -> None:
    """
    Placeholder to notify the FastAPI toolrunner service that a run should stop.
    """
    logger.info("Signal toolrunner cancel for run %s", run_id)
