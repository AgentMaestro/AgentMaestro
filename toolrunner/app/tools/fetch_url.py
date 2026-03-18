from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from fastapi.responses import JSONResponse

from ..config import WEB_FETCH_MAX_BYTES, WEB_FETCH_TIMEOUT_SECONDS
from ..models import FetchUrlArgs
from ..networking import UnsafeUrlError, ensure_safe_public_url

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        cleaned = " ".join(str(data or "").split())
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        text = " ".join(self.parts)
        return re.sub(r"\n\s+", "\n", text).strip()


def run_fetch_url(_run_dir, args: FetchUrlArgs, _policy: dict | None = None):
    try:
        fetch_result = _fetch_url(args.url)
    except UnsafeUrlError as exc:
        return _error("UNSAFE_URL", str(exc))
    except httpx.TimeoutException:
        return _error("TIMEOUT", "fetch_url request timed out")
    except httpx.HTTPError as exc:
        return _error("REQUEST_FAILED", f"fetch_url request failed: {exc}")

    status_code = int(fetch_result["status_code"])
    if status_code >= 400:
        return _error("HTTP_ERROR", f"fetch_url returned HTTP {status_code}", {"status_code": status_code})

    body = bytes(fetch_result["body"])
    content_type = str(fetch_result["content_type"])
    final_url = str(fetch_result["final_url"])
    encoding = str(fetch_result.get("encoding") or "utf-8")
    title = ""

    if "html" in content_type:
        decoded = body.decode(encoding, errors="replace")
        title = _extract_title(decoded)
        content = _extract_main_text(decoded)
    elif "json" in content_type:
        parsed = json.loads(body.decode(encoding, errors="replace"))
        content = json.dumps(parsed, indent=2, ensure_ascii=False)
    else:
        content = body.decode(encoding, errors="replace")

    content = content.strip()
    truncated = bool(fetch_result["download_truncated"])
    if len(content) > args.max_chars:
        content = content[: args.max_chars].rstrip() + "?"
        truncated = True

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "url": args.url,
                "final_url": final_url,
                "title": title,
                "content": content,
                "content_type": content_type,
                "status_code": status_code,
                "truncated": truncated,
            },
        },
    )


def _fetch_url(url: str) -> dict[str, object]:
    current_url = url
    with httpx.Client(
        timeout=WEB_FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": "AgentMaestroToolRunner/1.0"},
    ) as client:
        for _ in range(5):
            ensure_safe_public_url(current_url)
            with client.stream("GET", current_url, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        break
                    current_url = urljoin(str(response.request.url), location)
                    continue
                content_type = response.headers.get("content-type", "application/octet-stream")
                encoding = response.encoding or "utf-8"
                body, download_truncated = _read_capped_body(response)
                return {
                    "status_code": response.status_code,
                    "final_url": str(response.request.url),
                    "content_type": content_type,
                    "encoding": encoding,
                    "body": body,
                    "download_truncated": download_truncated,
                }
    raise httpx.HTTPError("Too many redirects")


def _read_capped_body(response: httpx.Response) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        next_total = total + len(chunk)
        if next_total > WEB_FETCH_MAX_BYTES:
            remaining = WEB_FETCH_MAX_BYTES - total
            if remaining > 0:
                chunks.append(chunk[:remaining])
            truncated = True
            break
        chunks.append(chunk)
        total = next_total
    return b"".join(chunks), truncated


def _extract_title(document: str) -> str:
    match = _TITLE_RE.search(document)
    if not match:
        return ""
    return html.unescape(" ".join(match.group(1).split())).strip()


def _extract_main_text(document: str) -> str:
    try:
        import trafilatura  # type: ignore
    except Exception:
        trafilatura = None
    if trafilatura is not None:
        extracted = trafilatura.extract(
            document,
            include_links=False,
            include_images=False,
            output_format="txt",
        )
        if extracted:
            return extracted.strip()
    parser = _TextExtractor()
    parser.feed(document)
    return parser.text()


def _error(code: str, message: str, details: dict | None = None):
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": details or {},
            },
        },
    )
