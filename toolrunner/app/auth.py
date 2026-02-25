from __future__ import annotations

import hmac
import time
from hashlib import sha256

from fastapi import HTTPException, Request, status

from .config import SECRET, TIMESTAMP_SKEW_SECONDS


def verify_signature(request: Request) -> bytes:
    raw = request.state._body
    timestamp = request.headers.get("X-AM-Timestamp")
    if not timestamp:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing timestamp")
    signature = request.headers.get("X-AM-Signature")
    if not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing signature")
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid timestamp")
    current = int(time.time())
    if abs(current - ts) > TIMESTAMP_SKEW_SECONDS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Stale timestamp")
    message = timestamp.encode("utf-8") + b"." + raw
    expected = hmac.new(SECRET, message, sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    return raw
