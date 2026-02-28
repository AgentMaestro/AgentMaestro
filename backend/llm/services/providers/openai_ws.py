import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import websockets

from .base import OPENAI_WS_DEBUG

logger = logging.getLogger(__name__)


def _build_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}/v1/responses"


def _auth_headers(api_key: str) -> List[Tuple[str, str]]:
    return [("Authorization", f"Bearer {api_key}")]


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
    }


class OpenAIResponsesWSException(RuntimeError):
    pass


class OpenAIResponsesWSPreviousResponseNotFound(OpenAIResponsesWSException):
    pass


class OpenAIResponsesWSConnectionLimitReached(OpenAIResponsesWSException):
    pass


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
        idle_timeout_seconds: float = 60.0,
        timeout_seconds: float = 120.0,
    ):
        base_url = base_url.rstrip("/") if base_url else "https://api.openai.com"
        self._url = _build_ws_url(base_url)
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self.previous_response_id: Optional[str] = None
        self._last_active = time.monotonic()
        self._idle_timeout_seconds = idle_timeout_seconds
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connect_lock = asyncio.Lock()

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
        async with self._connect_lock:
            if self._ws and not self._connection_closed():
                return
            _log_debug("OpenAI WS connecting to %s", self._url)
            headers = _auth_headers(self._api_key)
            self._ws = await websockets.connect(
                self._url,
                additional_headers=headers,
                ping_interval=None,
                compression=None,
                open_timeout=self._timeout_seconds,
                user_agent_header="AgentMaestro/1.0",
            )
            self._mark_active()

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
            "input": input_items,
        }
        if tools:
            payload["tools"] = tools
        if self.previous_response_id:
            payload["previous_response_id"] = self.previous_response_id
        retries = 0
        while True:
            await self._ensure_connection()
            try:
                _log_debug(
                    "OpenAI WS send payload type=%s model=%s has_tools=%s has_previous=%s input_count=%d",
                    payload.get("type"),
                    payload.get("model"),
                    bool(payload.get("tools")),
                    bool(payload.get("previous_response_id")),
                    len(payload.get("input") or []),
                )
                await self._ws.send(json.dumps(payload))
                response = await self._receive_until_complete()
                normalized = _normalize_response(response)
                resp_id = normalized.get("response_id")
                if isinstance(resp_id, str) and resp_id:
                    self.previous_response_id = resp_id
                self._mark_active()
                return normalized
            except OpenAIResponsesWSConnectionLimitReached:
                retries += 1
                await self.close()
                if retries > 1:
                    raise
                continue
            except asyncio.TimeoutError as exc:
                retries += 1
                await self.close()
                if retries <= 1:
                    continue
                raise OpenAIResponsesWSException("OpenAI WS timeout") from exc
            except websockets.WebSocketException as exc:
                await self.close()
                raise OpenAIResponsesWSException("OpenAI WS connection failed") from exc

    async def _receive_until_complete(self) -> Dict[str, Any]:
        assert self._ws
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout_seconds)
            self._mark_active()
            event = json.loads(raw)
            event_type = event.get("type")
            _log_debug("OpenAI WS event type=%s", event_type)
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
            if event_type == "response.error":
                error = event.get("error") or {}
                code = error.get("code")
                message = error.get("message") or "response.error from OpenAI WS"
                if code == "previous_response_not_found":
                    self.previous_response_id = None
                    raise OpenAIResponsesWSPreviousResponseNotFound(message)
                if code == "websocket_connection_limit_reached":
                    raise OpenAIResponsesWSConnectionLimitReached(message)
                raise OpenAIResponsesWSException(message)


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
        self._sessions: Dict[str, OpenAIResponsesWebSocketSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, run_id: str, model: str) -> OpenAIResponsesWebSocketSession:
        async with self._lock:
            session = self._sessions.get(run_id)
            if session and session.model != model:
                await session.close()
                session = None
            if not session:
                session = OpenAIResponsesWebSocketSession(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    model=model,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                    timeout_seconds=self._timeout_seconds,
                )
                self._sessions[run_id] = session
        await session.connect()
        return session

    async def close(self, run_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(run_id, None)
        if session:
            await session.close()

    async def cleanup(self) -> None:
        now = time.monotonic()
        sessions_to_close: List[OpenAIResponsesWebSocketSession] = []
        async with self._lock:
            for run_id, session in list(self._sessions.items()):
                if now - session.last_activity > self._idle_timeout_seconds:
                    sessions_to_close.append(session)
                    del self._sessions[run_id]
        for session in sessions_to_close:
            await session.close()

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close()
