from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.tooling.registry import ToolRegistry
from core.tooling.types import ToolContext
from core.tooling.web import html_to_text, web_tools


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/json":
            body = b'{"answer": 42}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path == "/empty":
            body = b""
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        elif self.path == "/missing":
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        else:
            body = (
                b"<html><head><title>T</title><style>.x{}</style></head>"
                b"<body><h1>Heading</h1><p>Hello <b>world</b>.</p>"
                b"<script>alert(1)</script></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def server() -> str:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(web_tools())
    return registry


def test_html_to_text_strips_scripts_and_styles() -> None:
    text = html_to_text(
        "<h1>Title</h1><script>bad()</script><p>Hello <b>world</b></p>"
    )
    assert "Title" in text
    assert "Hello world" in text
    assert "bad()" not in text


@pytest.mark.asyncio
async def test_webfetch_html(server: str, registry: ToolRegistry, tmp_path: Path) -> None:
    context = ToolContext(root=tmp_path)
    result = await registry.execute("webfetch", {"url": server + "/"}, context)
    assert result.ok
    assert "Heading" in result.output
    assert "Hello world" in result.output
    assert "alert(1)" not in result.output
    assert result.metadata["content_type"].startswith("text/html")


@pytest.mark.asyncio
async def test_webfetch_json(server: str, registry: ToolRegistry, tmp_path: Path) -> None:
    context = ToolContext(root=tmp_path)
    result = await registry.execute("webfetch", {"url": server + "/json"}, context)
    assert result.ok
    assert '"answer": 42' in result.output


@pytest.mark.asyncio
async def test_webfetch_http_error(server: str, registry: ToolRegistry, tmp_path: Path) -> None:
    context = ToolContext(root=tmp_path)
    result = await registry.execute("webfetch", {"url": server + "/missing"}, context)
    assert not result.ok
    assert "HTTP 404" in result.output


@pytest.mark.asyncio
async def test_webfetch_rejects_bad_scheme(registry: ToolRegistry, tmp_path: Path) -> None:
    context = ToolContext(root=tmp_path)
    for url in ("file:///etc/passwd", "ftp://example.com", "not-a-url"):
        result = await registry.execute("webfetch", {"url": url}, context)
        assert not result.ok
        assert "Only http" in result.output or "Invalid URL" in result.output
