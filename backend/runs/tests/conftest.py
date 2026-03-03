import os

import pytest
from django.conf import settings


@pytest.fixture(autouse=True)
def disable_rate_limits_env(request):
    # Only disable rate limits for tests executed under runs/tests
    path = str(request.fspath).replace("\\", "/")
    if "runs/tests" not in path:
        yield
        return

    previous = os.environ.get("DISABLE_RATE_LIMITS")
    os.environ["DISABLE_RATE_LIMITS"] = "1"
    settings.DISABLE_RATE_LIMITS = True
    yield
    if previous is None:
        os.environ.pop("DISABLE_RATE_LIMITS", None)
        settings.DISABLE_RATE_LIMITS = False
    else:
        os.environ["DISABLE_RATE_LIMITS"] = previous
        settings.DISABLE_RATE_LIMITS = previous.lower() in {"1", "true", "yes"}
