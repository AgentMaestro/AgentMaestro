from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def _extract_db_from_url(url: str) -> Optional[int]:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    path = (parsed.path or "").lstrip("/")
    if path.isdigit():
        return int(path)

    if parsed.query:
        try:
            params = parse_qs(parsed.query)
            db_values = params.get("db") or params.get("DATABASE")
            if db_values:
                candidate = db_values[0]
                if candidate.isdigit():
                    return int(candidate)
        except Exception:
            pass
    return None


def validate_redis_db(url: str | None, expected_db: int, label: str) -> None:
    if not url:
        logger.warning("Redis URL missing for %s; expected db=%s", label, expected_db)
        return

    db = _extract_db_from_url(url)
    if db is None:
        logger.warning(
            "Redis URL %s for %s does not explicitly specify a DB; expected db=%s",
            url,
            label,
            expected_db,
        )
        return

    if db != expected_db:
        logger.error(
            "Redis URL %s for %s uses db=%s but expected db=%s",
            url,
            label,
            db,
            expected_db,
        )
