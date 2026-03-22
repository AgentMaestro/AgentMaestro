from core.services.limits import LimitKey, QUOTA_MANAGER


def acquire_tool_call_slots(workspace_id: str, run_id: str, member: str) -> None:
    # Keep only the workspace-level cap here. The per-run gate was too aggressive
    # for multi-tool approval flows and caused the second request in a batch to fail.
    QUOTA_MANAGER.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_TOOL_CALLS_WS, member)


def release_tool_call_slots(workspace_id: str, run_id: str, member: str) -> None:
    QUOTA_MANAGER.release_concurrency(workspace_id, LimitKey.CONCURRENT_TOOL_CALLS_WS, member)
