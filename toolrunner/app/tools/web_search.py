from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi.responses import JSONResponse

from ..config import BRAVE_SEARCH_API_KEY, WEB_SEARCH_PROVIDER, WEB_SEARCH_TIMEOUT_SECONDS
from ..models import WebSearchArgs

logger = logging.getLogger(__name__)

_MAX_BRAVE_LOG_CHARS = 4000


class WebSearchError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str


class WebSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]:
        ...


class BraveWebSearchProvider:
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        response = None
        try:
            with httpx.Client(timeout=WEB_SEARCH_TIMEOUT_SECONDS) as client:
                response = client.get(
                    self.endpoint,
                    params={
                        "q": query,
                        "count": max_results,
                        "text_decorations": False,
                        "search_lang": "en",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self.api_key,
                    },
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise WebSearchError("TIMEOUT", "web_search request timed out") from exc
        except httpx.HTTPStatusError as exc:
            _log_brave_response(
                query=query,
                max_results=max_results,
                response=exc.response,
                note="Brave search HTTP error",
            )
            raise WebSearchError(
                "HTTP_ERROR",
                f"web_search provider returned HTTP {exc.response.status_code}",
                {"status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise WebSearchError("REQUEST_FAILED", f"web_search request failed: {exc}") from exc

        payload = response.json()
        results = (((payload or {}).get("web") or {}).get("results") or [])[:max_results]
        if not results:
            _log_brave_response(
                query=query,
                max_results=max_results,
                response=response,
                payload=payload,
                note="Brave search returned no results",
            )
        normalized: list[SearchResult] = []
        for item in results:
            normalized.append(
                SearchResult(
                    title=str(item.get("title") or "").strip(),
                    url=str(item.get("url") or "").strip(),
                    snippet=str(item.get("description") or item.get("snippet") or "").strip(),
                    source="brave",
                )
            )
        return normalized


def run_web_search(_run_dir, args: WebSearchArgs, _policy: dict | None = None):
    try:
        provider = _get_provider()
        results = provider.search(args.query, args.max_results)
    except WebSearchError as exc:
        return _error(exc.code, exc.message, exc.details)
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "query": args.query,
                "results": [result.__dict__ for result in results],
            },
        },
    )


def _get_provider() -> WebSearchProvider:
    provider_name = str(WEB_SEARCH_PROVIDER or "brave").strip().lower()
    if provider_name != "brave":
        raise WebSearchError("CONFIG_ERROR", f"Unsupported web search provider '{provider_name}'")
    if not BRAVE_SEARCH_API_KEY:
        raise WebSearchError("CONFIG_ERROR", "BRAVE_SEARCH_API_KEY is not configured")
    return BraveWebSearchProvider(BRAVE_SEARCH_API_KEY)


def _error(code: str, message: str, details: dict | None = None):
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "error": {"code": f"tool_runner.{code}", "message": message, "details": details or {}},
        },
    )


def _log_brave_response(
    *,
    query: str,
    max_results: int,
    response: httpx.Response | None,
    payload: object | None = None,
    note: str,
) -> None:
    body_text = ""
    if payload is None and response is not None:
        try:
            payload = response.json()
        except Exception:
            body_text = _trim_text(response.text or "", _MAX_BRAVE_LOG_CHARS)
    if payload is not None and not body_text:
        try:
            body_text = _trim_text(json.dumps(payload, ensure_ascii=False), _MAX_BRAVE_LOG_CHARS)
        except Exception:
            body_text = _trim_text(str(payload), _MAX_BRAVE_LOG_CHARS)
    logger.warning(
        "%s query=%r max_results=%s status=%s body=%s",
        note,
        query,
        max_results,
        getattr(response, "status_code", None) if response is not None else None,
        body_text,
    )


def _trim_text(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
