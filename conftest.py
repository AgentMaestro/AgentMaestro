from __future__ import annotations

import os
from pathlib import Path

import pytest


SHARED_BASE = Path(__file__).resolve().parent / "toolrunner" / "pytest_temp"


def _normalize_shared_base() -> str:
    env_path = os.environ.get("PYTEST_BASETEMP")
    base = Path(env_path) if env_path else SHARED_BASE
    base.parent.mkdir(parents=True, exist_ok=True)
    return str(base.resolve())


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if config.option.basetemp:
        return
    config.option.basetemp = _normalize_shared_base()
