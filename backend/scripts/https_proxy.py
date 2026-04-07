from __future__ import annotations

import argparse
import http.client
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from ssl import SSLContext, PROTOCOL_TLS_SERVER
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from logging_utils import get_app_logger


logger = get_app_logger("scripts.https_proxy")

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _split_backend_url(backend_url: str) -> tuple[str, int, str, bool]:
    parsed = urlsplit(backend_url)
    scheme = (parsed.scheme or "http").lower()
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if scheme == "https" else 80)
    base_path = parsed.path.rstrip("/")
    use_https = scheme == "https"
    return host, port, base_path, use_https


def _build_backend_path(base_path: str, request_path: str) -> str:
    request_path = request_path or "/"
    if base_path:
        if request_path == "/":
            return base_path + "/"
        return f"{base_path}{request_path}"
    return request_path


def _forward_request(handler: BaseHTTPRequestHandler, backend_host: str, backend_port: int, backend_base_path: str, backend_https: bool) -> None:
    content_length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(content_length) if content_length else None
    backend_path = _build_backend_path(backend_base_path, handler.path)

    request_headers: dict[str, str] = {}
    for key, value in handler.headers.items():
        header_name = key.lower()
        if header_name in HOP_BY_HOP_HEADERS or header_name == "host":
            continue
        request_headers[key] = value
    request_headers["Host"] = backend_host

    connection: http.client.HTTPConnection
    if backend_https:
        connection = http.client.HTTPSConnection(backend_host, backend_port, timeout=30)
    else:
        connection = http.client.HTTPConnection(backend_host, backend_port, timeout=30)

    logger.info("proxy %s %s -> %s:%s%s", handler.command, handler.path, backend_host, backend_port, backend_path)
    try:
        connection.request(handler.command, backend_path, body=body, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        handler.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            if key.lower() == "content-length":
                continue
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(response_body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(response_body)
    finally:
        connection.close()


def build_handler(backend_host: str, backend_port: int, backend_base_path: str, backend_https: bool):
    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            _forward_request(self, backend_host, backend_port, backend_base_path, backend_https)

        def do_POST(self) -> None:  # noqa: N802
            _forward_request(self, backend_host, backend_port, backend_base_path, backend_https)

        def do_HEAD(self) -> None:  # noqa: N802
            _forward_request(self, backend_host, backend_port, backend_base_path, backend_https)

        def do_PUT(self) -> None:  # noqa: N802
            _forward_request(self, backend_host, backend_port, backend_base_path, backend_https)

        def do_DELETE(self) -> None:  # noqa: N802
            _forward_request(self, backend_host, backend_port, backend_base_path, backend_https)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            logger.info("proxy %s", format % args)

    return ProxyHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Local HTTPS reverse proxy for AgentMaestro dev callbacks.")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8443)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cert-file", required=True)
    parser.add_argument("--key-file", required=True)
    args = parser.parse_args()

    backend_host, backend_port, backend_base_path, backend_https = _split_backend_url(args.backend_url)
    handler_cls = build_handler(backend_host, backend_port, backend_base_path, backend_https)
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), handler_cls)
    context = SSLContext(PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=args.cert_file, keyfile=args.key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    logger.info(
        "proxy listening on https://%s:%s -> %s://%s:%s%s",
        args.listen_host,
        args.listen_port,
        "https" if backend_https else "http",
        backend_host,
        backend_port,
        backend_base_path or "",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("proxy shutdown requested")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
