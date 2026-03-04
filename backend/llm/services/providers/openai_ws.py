import asyncio
import json
import os
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import websockets

from .base import OPENAI_WS_DEBUG
from logging_utils import get_app_logger

logger = get_app_logger(__name__)
logger.info("websockets version=%s", getattr(websockets, "__version__", "unknown"))
OPENAI_WS_MAX_RETRIES = int(os.getenv("OPENAI_WS_MAX_RETRIES", "3"))
OPENAI_WS_MAX_RETRY_JITTER_MS = int(os.getenv("OPENAI_WS_MAX_RETRY_JITTER_MS", "250"))

VALIDATION_ERROR_CODES = {
    "oneofparam",
    "invalid_enum_value",
    "invalid_value",
    "missing_required_parameter",
    "invalid_format",
    "invalid_parameter",
}
RATE_LIMIT_ERROR_CODES = {
    "rate_limit_exceeded",
    "requests_per_minute_limit",
    "too_many_requests",
}
RATE_LIMIT_STATUS_CODES = {429, 502, 503, 504}


def _backoff_delay(attempt: int) -> float:
    base = 1 << min(attempt, 2)
    jitter = random.uniform(0, OPENAI_WS_MAX_RETRY_JITTER_MS) / 1000
    return base + jitter


def _classify_error(code: Optional[str], message: Optional[str], status: Optional[int]) -> str:
    code = (code or "").lower()
    message = (message or "").lower()
    if code in VALIDATION_ERROR_CODES or "invalid" in message:
        return "validation_error"
    if code in RATE_LIMIT_ERROR_CODES or status in RATE_LIMIT_STATUS_CODES or "rate limit" in message:
        return "ratelimit"
    return "unknown"


def _extract_request_id(event: Dict[str, Any]) -> Optional[str]:
    response = event.get("response") or {}
    metadata = response.get("metadata") or event.get("metadata") or {}
    return metadata.get("request_id")


PING_INTERVAL_SECONDS = 25

def _build_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}/v1/responses"


def _auth_headers(api_key: str) -> List[Tuple[str, str]]:
    headers = [("Authorization", f"Bearer {api_key}")]
    beta_header = os.environ.get("OPENAI_WS_BETA_HEADER", "responses=ws")
    if beta_header:
        headers.append(("OpenAI-Beta", beta_header))
    return headers


def _mask_headers(headers: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    masked: List[Tuple[str, str]] = []
    for name, value in headers:
        if name.lower() == "authorization" and value.lower().startswith("bearer "):
            token = value[7:]
            masked_token = token[:6] + "…" + token[-4:] if len(token) > 10 else token
            masked.append((name, f"Bearer {masked_token}"))
        else:
            masked.append((name, value))
    return masked


def _log_debug(msg: str, *args: Any) -> None:
    if OPENAI_WS_DEBUG:
        logger.debug(msg, *args)


def _normalize_call_id(payload: Dict[str, Any]) -> str:
    for key in ("call_id", "id", "tool_call_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _format_arguments(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def _extract_tool_call_payload(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    function_payload = item.get("function")
    payload = function_payload if isinstance(function_payload, dict) else item
    call_id = _normalize_call_id(payload)
    if not call_id:
        return None
    name = payload.get("name") or item.get("name") or ""
    arguments = (
        payload.get("arguments")
        or payload.get("input")
        or item.get("arguments")
        or item.get("input")
        or ""
    )
    return {"call_id": call_id, "name": name, "arguments": arguments}


def _collect_text(response: Dict[str, Any]) -> str:
    chunks: List[str] = []

    def add_text(value: Any) -> None:
        if isinstance(value, str):
            chunks.append(value)
            return
        if isinstance(value, dict):
            text_value = value.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value)
            for key in ("content", "items", "output", "output_text"):
                if key in value:
                    add_text(value[key])
            return
        if isinstance(value, list):
            for item in value:
                add_text(item)

    add_text(response.get("output_text") or [])
    add_text(response.get("output") or [])
    return "".join(chunks).strip()


def _collect_tool_calls(response: Dict[str, Any]) -> List[Dict[str, str]]:
    outputs = response.get("output") or []
    calls: List[Dict[str, str]] = []
    if not isinstance(outputs, list):
        return calls
    for item in outputs:
        if not isinstance(item, dict):
            continue
        call_type = item.get("type")
        if call_type not in {"function_call", "tool_call", "custom_tool_call"}:
            continue
        payload = _extract_tool_call_payload(item)
        if not payload:
            continue
        calls.append(
            {
                "id": payload["call_id"],
                "name": payload["name"],
                "arguments": _format_arguments(payload["arguments"]),
            }
        )
    return calls


def _normalize_response(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "response_id": response.get("id"),
        "text": _collect_text(response),
        "tool_calls": _collect_tool_calls(response),
        "raw": response,
        "model": response.get("model"),
        "request_id": (response.get("metadata") or {}).get("request_id"),
    }


class OpenAIResponsesWSException(RuntimeError):
    classification = "unknown"

    def __init__(self, message, *, code=None, param=None, status=None, request_id=None):
        super().__init__(message)
        self.code = code
        self.param = param
        self.status = status
        self.request_id = request_id


class OpenAIResponsesWSValidationError(OpenAIResponsesWSException):
    classification = "validation_error"


class OpenAIResponsesWSRateLimitError(OpenAIResponsesWSException):
    classification = "ratelimit"


class OpenAIResponsesWSNetworkError(OpenAIResponsesWSException):
    classification = "network_error"


class OpenAIResponsesWSPreviousResponseNotFound(OpenAIResponsesWSException):
    classification = "prev_not_found"


class OpenAIResponsesWSConnectionLimitReached(OpenAIResponsesWSException):
    classification = "connection_limit"


class OpenAIResponsesWSClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") if base_url else "https://api.openai.com"
        self.timeout = timeout

    async def create_response(
        self, *, model: str, input_text: str, system_text: Optional[str] = None
    ) -> Dict[str, Any]:
        url = _build_ws_url(self.base_url)
        input_items: List[Dict[str, Any]] = []
        if system_text:
            input_items.append(
                {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_text}],
                }
            )
        input_items.append(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": input_text}],
            }
        )
        payload: Dict[str, Any] = {
            "type": "response.create",
            "model": model,
            "input": input_items,
        }

        headers = _auth_headers(self.api_key)
        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=None,
                compression=None,
                open_timeout=self.timeout,
                user_agent_header="AgentMaestro/1.0",
            ) as socket:
                await socket.send(json.dumps(payload))
                while True:
                    raw = await asyncio.wait_for(socket.recv(), timeout=self.timeout)
                    event = json.loads(raw)
                    if event.get("type") == "response.completed":
                        response = event.get("response", {})
                        return _normalize_response(response)
                    if event.get("type") == "response.error":
                        error = event.get("error", {})
                        raise RuntimeError(error.get("message") or "response.error from OpenAI WS")
        except asyncio.TimeoutError as exc:
            raise RuntimeError("OpenAI WS timeout") from exc
        except websockets.WebSocketException as exc:
            raise RuntimeError("OpenAI WS connection failed") from exc


class OpenAIResponsesWebSocketSession:
    def __init__(
        self,
        api_key: str,
        base_url: Optional[str],
        model: str,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        idle_timeout_seconds: float = 60.0,
        timeout_seconds: float = 120.0,
    ):
        base_url = base_url.rstrip("/") if base_url else "https://api.openai.com"
        self._url = _build_ws_url(base_url)
        self._api_key = api_key
        self._model = model
        self._run_id = run_id or "unknown"
        self._agent_id = agent_id or "unknown"
        self._timeout_seconds = timeout_seconds
        self.previous_response_id: Optional[str] = None
        self._last_active = time.monotonic()
        self._idle_timeout_seconds = idle_timeout_seconds
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connect_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._first_event_logged = False

        logger.debug("-------------- WS START DEBUG LOGS ----------------------")
        logger.debug(f"url = {self._url}")
        logger.debug(f"openai_api_key = {self._api_key}")
        logger.debug(f"model = {self._model}")
        logger.debug(f"ws = {self._ws}")
        logger.debug("-------------- WS END DEBUG LOGS ----------------------")

    @property
    def model(self) -> str:
        return self._model

    @property
    def idle_timeout_seconds(self) -> float:
        return self._idle_timeout_seconds

    @property
    def last_activity(self) -> float:
        return self._last_active

    def _mark_active(self) -> None:
        self._last_active = time.monotonic()

    async def connect(self) -> None:
        await self.ensure_connected()

    async def ensure_connected(self) -> None:
        async with self._connect_lock:
            if self._ws and not self._connection_closed():
                return
            logger.info(
                "OpenAI WS connecting run=%s model=%s url=%s",
                self._run_id,
                self._model,
                self._url,
            )
            logger.info("OpenAI WS full URL: %s", self._url)
            headers = _auth_headers(self._api_key)
            logger.debug("OpenAI WS headers %s", _mask_headers(headers))
            try:
                self._ws = await websockets.connect(
                    self._url,
                    additional_headers=headers,
                    ping_interval=PING_INTERVAL_SECONDS,
                    compression=None,
                    open_timeout=self._timeout_seconds,
                    user_agent_header="AgentMaestro/1.0",
                )
            except websockets.WebSocketException as exc:
                logger.error(
                    "OpenAI WS connect error run=%s model=%s url=%s: %s",
                    self._run_id,
                    self._model,
                    self._url,
                    exc,
                )
                raise
            self._mark_active()
            logger.info(
                "OpenAI WS connected run=%s model=%s url=%s",
                self._run_id,
                self._model,
                self._url,
            )

    async def close(self) -> None:
        if self._ws and not self._connection_closed():
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    def _connection_closed(self) -> bool:
        if not self._ws:
            return True
        closed_attr = getattr(self._ws, "closed", None)
        if isinstance(closed_attr, bool):
            return closed_attr
        state = getattr(self._ws, "state", None)
        if state is not None:
            return state == websockets.State.CLOSED
        return True

    def _log_event_metadata(self, event: Dict[str, Any], event_type: str | None) -> None:
        response_ref = ""
        response = event.get("response") or {}
        if isinstance(response, dict):
            response_ref = response.get("id") or response.get("response_id") or ""
        if not response_ref:
            response_ref = event.get("response_id") or event.get("id") or ""
        previous_ref = self.previous_response_id or ""
        logger.info(
            "OpenAI WS event run=%s model=%s type=%s response_id=%s previous_response_id_sent=%s",
            self._run_id,
            self._model,
            event_type,
            response_ref,
            previous_ref,
        )

    def _log_send_event(self, request_id: str, attempt: int, payload: Dict[str, Any]) -> None:
        tool_count = len(payload.get("tools") or [])
        input_count = len(payload.get("input") or [])
        logger.info(
            "OpenAI WS send run=%s agent=%s model=%s request_id=%s previous_response_id=%s attempt=%d inputs=%d tools=%d",
            self._run_id,
            self._agent_id,
            self._model,
            request_id,
            payload.get("previous_response_id"),
            attempt,
            input_count,
            tool_count,
        )

    def _log_receive_event(
        self,
        response: Dict[str, Any],
        request_id: str,
        latency: float,
        classification: str,
    ) -> None:
        response_id = (
            response.get("id")
            or response.get("response_id")
            or response.get("metadata", {}).get("response_id")
            or ""
        )
        model_returned = response.get("model") or self._model
        logger.info(
            "OpenAI WS recv run=%s agent=%s model=%s request_id=%s response_id=%s model_returned=%s latency=%.3f classification=%s",
            self._run_id,
            self._agent_id,
            self._model,
            request_id,
            response_id,
            model_returned,
            latency,
            classification,
        )

    def _log_failure_event(
        self,
        classification: str,
        request_id: str,
        attempt: int,
        error: OpenAIResponsesWSException,
    ) -> None:
        logger.warning(
            "OpenAI WS failure run=%s agent=%s model=%s request_id=%s classification=%s attempt=%d error=%s param=%s",
            self._run_id,
            self._agent_id,
            self._model,
            error.request_id or request_id,
            classification,
            attempt,
            str(error),
            error.param,
        )
    def _reconnect_needed(self) -> bool:
        return self._connection_closed()

    async def _ensure_connection(self) -> None:
        if self._reconnect_needed():
            await self.connect()

    async def create_or_continue(
        self,
        *,
        input_items: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not input_items:
            raise ValueError("input_items must be provided for responses.create")
        payload: Dict[str, Any] = {
            "type": "response.create",
            "model": self._model,
            "store": False,
            "input": input_items,
        }
        if tools:
            payload["tools"] = tools
        if self.previous_response_id:
            payload["previous_response_id"] = self.previous_response_id
        payload.setdefault("metadata", {})
        request_id = payload["metadata"].get("request_id") or str(uuid.uuid4())
        payload["metadata"]["request_id"] = request_id
        attempt = 0
        max_attempts = max(1, OPENAI_WS_MAX_RETRIES)
        while True:
            try:
                response, latency = await self._send_request(payload, request_id, attempt + 1)
            except OpenAIResponsesWSPreviousResponseNotFound as exc:
                self.previous_response_id = None
                self._log_failure_event(exc.classification, request_id, attempt + 1, exc)
                await self.close()
                await asyncio.sleep(_backoff_delay(attempt))
                if attempt >= max_attempts - 1:
                    raise
                attempt += 1
                continue
            except OpenAIResponsesWSValidationError as exc:
                self._log_failure_event(exc.classification, request_id, attempt + 1, exc)
                raise
            except OpenAIResponsesWSRateLimitError as exc:
                self._log_failure_event(exc.classification, request_id, attempt + 1, exc)
                attempt += 1
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(_backoff_delay(attempt - 1))
                continue
            except OpenAIResponsesWSNetworkError as exc:
                self._log_failure_event(exc.classification, request_id, attempt + 1, exc)
                attempt += 1
                await self.close()
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(_backoff_delay(attempt - 1))
                continue
            except OpenAIResponsesWSException as exc:
                self._log_failure_event(exc.classification, request_id, attempt + 1, exc)
                attempt += 1
                await self.close()
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(_backoff_delay(attempt - 1))
                continue
            normalized = _normalize_response(response)
            resp_id = normalized.get("response_id")
            if isinstance(resp_id, str) and resp_id:
                self.previous_response_id = resp_id
            self._mark_active()
            self._log_receive_event(response, request_id, latency, classification="success")
            return normalized

    async def _send_request(
        self, payload: Dict[str, Any], request_id: str, attempt: int
    ) -> tuple[Dict[str, Any], float]:
        await self._ensure_connection()
        async with self._io_lock:
            payload["previous_response_id"] = self.previous_response_id
            payload_json = json.dumps(payload, ensure_ascii=False)
            self._log_send_event(request_id, attempt, payload)
            start = time.monotonic()
            logger.debug("OpenAI WS send payload run=%s model=%s", self._run_id, self._model)
            try:
                await self._ws.send(payload_json)
                response = await self._receive_until_complete()
            except asyncio.TimeoutError as exc:
                raise OpenAIResponsesWSNetworkError(
                    "OpenAI WS timeout", status=None, request_id=request_id
                ) from exc
            except websockets.WebSocketException as exc:
                raise OpenAIResponsesWSNetworkError(
                    "OpenAI WS connection failed during send", status=None, request_id=request_id
                ) from exc
            latency = time.monotonic() - start
            return response, latency

    async def _receive_until_complete(self) -> Dict[str, Any]:
        assert self._ws
        self._first_event_logged = False
        while True:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout_seconds)
            except websockets.exceptions.ConnectionClosed as exc:
                logger.warning(
                    "OpenAI WS closed run=%s agent=%s model=%s code=%s reason=%r was_clean=%s",
                    self._run_id,
                    self._agent_id,
                    self._model,
                    getattr(exc, "code", None),
                    getattr(exc, "reason", None),
                    isinstance(exc, websockets.exceptions.ConnectionClosedOK),
                )
                raise OpenAIResponsesWSNetworkError(
                    "OpenAI WS connection closed without a completed response",
                    status=getattr(exc, "code", None),
                ) from exc
            self._mark_active()
            logger.debug(
                "OpenAI WS raw recv (first 2000 chars): %s",
                raw if isinstance(raw, str) else raw[:2000],
            )
            raw_preview = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            logger.debug(
                "OpenAI WS raw frame preview run=%s model=%s payload=%s",
                self._run_id,
                self._model,
                raw_preview[:2000],
            )
            try:
                event = json.loads(raw_preview)
            except Exception:
                logger.exception(
                    "OpenAI WS failed to parse JSON frame run=%s model=%s", self._run_id, self._model
                )
                raise
            event_type = event.get("type")
            self._log_event_metadata(event, event_type)
            if not self._first_event_logged:
                logger.info(
                    "OpenAI WS first event run=%s model=%s type=%s",
                    self._run_id,
                    self._model,
                    event_type,
                )
                self._first_event_logged = True
            _log_debug("OpenAI WS event type=%s", event_type)
            logger.debug(
                "OpenAI WS event payload run=%s model=%s event=%s",
                self._run_id,
                self._model,
                json.dumps(event, ensure_ascii=False)[:2000],
            )
            if event_type == "response.completed":
                response = event.get("response", {})
                text_summary = _collect_text(response)
                output_items = response.get("output")
                if isinstance(output_items, list):
                    output_item_count = len(output_items)
                else:
                    output_item_count = 1 if output_items else 0
                tool_call_count = len(_collect_tool_calls(response))
                _log_debug(
                    "OpenAI WS response.completed id=%s text_len=%d output_items=%d tool_call_count=%d",
                    response.get("id"),
                    len(text_summary),
                    output_item_count,
                    tool_call_count,
                )
                return response
            if event_type == "response.error" or event_type == "error":
                err = event.get("error") or {}
                code = err.get("code")
                message = err.get("message") or "OpenAI WS error"
                status = event.get("status")
                param = err.get("param")
                request_id = _extract_request_id(event)
                logger.error(
                    "OpenAI WS error run=%s agent=%s model=%s code=%s status=%s param=%s message=%s",
                    self._run_id,
                    self._agent_id,
                    self._model,
                    code,
                    status,
                    param,
                    message,
                )
                logger.debug(
                    "OpenAI WS error payload run=%s model=%s payload=%s",
                    self._run_id,
                    self._model,
                    json.dumps(event, ensure_ascii=False)[:2000],
                )
                if code == "previous_response_not_found":
                    raise OpenAIResponsesWSPreviousResponseNotFound(
                        message,
                        code=code,
                        param=param,
                        status=status,
                        request_id=request_id,
                    )
                if code == "websocket_connection_limit_reached":
                    raise OpenAIResponsesWSConnectionLimitReached(
                        message,
                        code=code,
                        param=param,
                        status=status,
                        request_id=request_id,
                    )
                classification = _classify_error(code, message, status)
                exc_kwargs = dict(code=code, param=param, status=status, request_id=request_id)
                if classification == "validation_error":
                    raise OpenAIResponsesWSValidationError(message, **exc_kwargs)
                if classification == "ratelimit":
                    raise OpenAIResponsesWSRateLimitError(message, **exc_kwargs)
                raise OpenAIResponsesWSException(message, **exc_kwargs)


class OpenAIResponsesWSSessionPool:
    def __init__(
        self,
        api_key: str,
        base_url: Optional[str],
        *,
        idle_timeout_seconds: float = 60.0,
        timeout_seconds: float = 120.0,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._idle_timeout_seconds = idle_timeout_seconds
        self._timeout_seconds = timeout_seconds
        self._sessions: Dict[tuple[str, str], OpenAIResponsesWebSocketSession] = {}
        self._lock = asyncio.Lock()

    async def get(
        self, run_id: str, model: str, *, agent_id: str | None = None
    ) -> OpenAIResponsesWebSocketSession:
        async with self._lock:
            key = (run_id, model)
            session = self._sessions.get(key)
            if session and session.model != model:
                await session.close()
                session = None
            if not session:
                session = OpenAIResponsesWebSocketSession(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    model=model,
                    run_id=run_id,
                    agent_id=agent_id,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                    timeout_seconds=self._timeout_seconds,
                )
                self._sessions[key] = session
        return session

    async def close(self, run_id: str, *, model: str | None = None) -> None:
        async with self._lock:
            if model is None:
                keys = [key for key in self._sessions if key[0] == run_id]
            else:
                keys = [(run_id, model)]
            sessions = [self._sessions.pop(key, None) for key in keys]
        for session in sessions:
            if session:
                await session.close()

    async def cleanup(self) -> None:
        now = time.monotonic()
        sessions_to_close: List[OpenAIResponsesWebSocketSession] = []
        async with self._lock:
            for key, session in list(self._sessions.items()):
                if now - session.last_activity > self._idle_timeout_seconds:
                    sessions_to_close.append(session)
                    del self._sessions[key]
        for session in sessions_to_close:
            await session.close()

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close()
