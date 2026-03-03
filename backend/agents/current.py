import threading
from contextlib import contextmanager
from typing import Generator, Optional

from django.contrib.auth import get_user_model

_local = threading.local()


def _get_current_user_attr() -> Optional[str]:
    return getattr(_local, "user", None)


def set_current_agent_creator(user) -> None:
    """Set temporary owner context for Agent creation."""
    _local.user = user


def get_current_agent_creator():
    return _get_current_user_attr()


def clear_current_agent_creator() -> None:
    if hasattr(_local, "user"):
        delattr(_local, "user")


@contextmanager
def agent_creation_context(user) -> Generator[None, None, None]:
    previous_user = _get_current_user_attr()
    set_current_agent_creator(user)
    try:
        yield
    finally:
        if previous_user is not None:
            set_current_agent_creator(previous_user)
        else:
            clear_current_agent_creator()
