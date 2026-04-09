from __future__ import annotations

import pytest

from core.models import Workspace
from finance.models import FinanceDataCacheEntry
from finance.services.source_summary import (
    mark_source_summary_queued,
    store_source_summary_result,
)


@pytest.mark.django_db
def test_source_summary_queue_writes_ai_summary_state():
    workspace = Workspace.objects.create(name="Source Summary Workspace")
    entry = FinanceDataCacheEntry.objects.create(
        cache_key="filings:AAPL:sec",
        workspace=workspace,
        data_kind=FinanceDataCacheEntry.DataKind.FILINGS,
        source_name="sec",
        payload={"filings": []},
        summary_text="SEC filings cached for finance research.",
    )

    result = mark_source_summary_queued(
        cache_key=entry.cache_key,
        source_url="https://www.sec.gov/example",
        source_title="10-K filed 2/24/26",
        source_kind="sec_filing",
        task_id="task-1",
        run_id="run-1",
    )

    assert result["ok"] is True
    entry.refresh_from_db()
    summary_state = entry.payload["ai_summaries"]["https://www.sec.gov/example"]
    assert summary_state["status"] == "queued"
    assert summary_state["source_title"] == "10-K filed 2/24/26"
    assert len(summary_state["summary_lines"]) >= 4


@pytest.mark.django_db
def test_source_summary_result_writes_ready_state_lines():
    workspace = Workspace.objects.create(name="Source Summary Workspace 2")
    entry = FinanceDataCacheEntry.objects.create(
        cache_key="filings:MSFT:sec",
        workspace=workspace,
        data_kind=FinanceDataCacheEntry.DataKind.FILINGS,
        source_name="sec",
        payload={"filings": []},
        summary_text="SEC filings cached for finance research.",
    )

    result = store_source_summary_result(
        cache_key=entry.cache_key,
        source_url="https://www.sec.gov/example-2",
        source_title="8-K filed 2/24/26",
        source_kind="sec_filing",
        summary_text="Line 1.\nLine 2.\nLine 3.\nLine 4.\nLine 5.\nLine 6.",
        run_id="run-2",
        task_id="task-2",
        error="",
    )

    assert result["ok"] is True
    assert result["status"] == "ready"
    entry.refresh_from_db()
    summary_state = entry.payload["ai_summaries"]["https://www.sec.gov/example-2"]
    assert summary_state["status"] == "ready"
    assert summary_state["summary_lines"] == ["Line 1.", "Line 2.", "Line 3.", "Line 4.", "Line 5.", "Line 6."]
