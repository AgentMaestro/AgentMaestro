from core.services.limits import LimitKey, QUOTA_MANAGER


def acquire_tool_call_slots(workspace_id: str, run_id: str, member: str) -> None:
    QUOTA_MANAGER.acquire_concurrency(workspace_id, LimitKey.CONCURRENT_TOOL_CALLS_WS, member)
    QUOTA_MANAGER.acquire_concurrency(run_id, LimitKey.CONCURRENT_TOOL_CALLS_RUN, member)


def release_tool_call_slots(workspace_id: str, run_id: str, member: str) -> None:
    QUOTA_MANAGER.release_concurrency(workspace_id, LimitKey.CONCURRENT_TOOL_CALLS_WS, member)
    QUOTA_MANAGER.release_concurrency(run_id, LimitKey.CONCURRENT_TOOL_CALLS_RUN, member)
