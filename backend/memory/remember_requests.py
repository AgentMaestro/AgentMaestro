from __future__ import annotations

import hashlib
import logging
import re
from textwrap import shorten

from django.contrib.auth import get_user_model

from memory.models import MemoryRecord
from memory.services import remember

logger = logging.getLogger(__name__)

EXPLICIT_USER_REMEMBER_SOURCE_KIND = "explicit_user_remember"
LOCAL_TIME_PREFERENCE_DEDUPE_KEY = "user-pref:local-time-reference"

_REMEMBER_PATTERNS = (
    re.compile(r"^\s*(?:please\s+)?remember\s+that\s+(?P<body>.+?)\s*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*note\s+that\s+(?P<body>.+?)\s*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*my\s+preference\s+is\s+(?P<body>.+?)\s*$", re.IGNORECASE | re.DOTALL),
)
_LOCAL_TIME_HINT_RE = re.compile(
    r"\b(local time|timezone|time zone|report time|i am in|i'm in|im in|located in|based in)\b",
    re.IGNORECASE,
)


def capture_explicit_user_memory_request(
    *,
    user,
    text: str,
    source_ref: str = "",
) -> MemoryRecord | None:
    resolved_user = _resolve_user(user)
    if resolved_user is None:
        return None
    remembered_text = extract_explicit_remember_text(text)
    if not remembered_text:
        return None

    is_local_time_preference = _looks_like_local_time_preference(remembered_text)
    dedupe_key = (
        LOCAL_TIME_PREFERENCE_DEDUPE_KEY
        if is_local_time_preference
        else f"explicit-remember:{hashlib.sha1(remembered_text.casefold().encode('utf-8')).hexdigest()[:16]}"
    )
    summary_prefix = "User local time preference" if is_local_time_preference else "User explicit memory"
    tags = ["remember-intent", "user-preference"]
    if is_local_time_preference:
        tags.extend(["time", "timezone", "location"])

    record = remember(
        user=resolved_user,
        memory_kind=MemoryRecord.MemoryKind.SEMANTIC,
        content=remembered_text,
        summary=f"{summary_prefix}: {shorten(remembered_text, width=96, placeholder='...')}",
        tags=tags,
        importance="0.85" if is_local_time_preference else "0.75",
        pinned=True,
        dedupe_key=dedupe_key,
        dedupe_mode="key",
        source_kind=EXPLICIT_USER_REMEMBER_SOURCE_KIND,
        source_ref=str(source_ref or "").strip(),
    )
    logger.info(
        "Captured explicit remember request user=%s memory_id=%s dedupe_key=%s source_ref=%s",
        resolved_user.pk,
        record.id,
        dedupe_key,
        str(source_ref or "").strip(),
    )
    return record


def extract_explicit_remember_text(text: str) -> str:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ""
    for pattern in _REMEMBER_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        body = " ".join((match.group("body") or "").split()).strip(" .")
        if len(body) < 8:
            return ""
        return body
    return ""


def _looks_like_local_time_preference(text: str) -> bool:
    return bool(_LOCAL_TIME_HINT_RE.search(str(text or "")))


def _resolve_user(user):
    if user is None:
        return None
    User = get_user_model()
    if isinstance(user, User):
        return user
    return User.objects.filter(pk=user).first()
