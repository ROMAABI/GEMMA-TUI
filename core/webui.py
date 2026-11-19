"""Local web UI server for Gemma.

Serves the built React frontend and the Gemma HTTP API from one origin, the way
OpenCode serves its web UI from the same server that powers the TUI. The server
shares the TUI's ``SessionStore`` and ``Settings``, so chats started in the
browser show up in the terminal and vice versa.

Access is protected by a one-time pairing link (valid for five minutes) that
sets an HttpOnly session cookie, mirroring ``opencode pair``.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
import re
import secrets
import threading
import time
import webbrowser
import httpx
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from config.settings import Settings
from core.agent_service import AgentRuntime
from core.attachments import recent_attachment_messages
from core.client import LlamaClient
from sessions.store import SessionStore
from utils import runlog
from utils.paths import server_log_path

DEFAULT_PORT = 47317
COOKIE_NAME = "gemma_web"
PAIR_TTL = 300
COOKIE_TTL = 60 * 60 * 24 * 30
MAX_BODY = 2 * 1024 * 1024

_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def _iso(value: str | None) -> str:
    """SQLite stores UTC timestamps without a zone; emit ISO-8601 with Z."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


CHAT_SYSTEM_PROMPT = """You are Gemma, a friendly assistant running inside the gemma app on the user's own computer.

In this chat mode you answer directly, without tools. The same app has an agent mode
(the tab key toggles it) that CAN run commands and edit files on this machine.

- Never claim that the user's computer is out of your reach or that nothing on it can be
  checked. The app you are running in has full shell and file access on this machine.
- For questions about the user's system — distro, OS version, kernel, files, disk, memory,
  processes, IP, installed packages — say that you will check with a real command and ask
  the user to switch to agent mode (press tab, ask again). Never guess or deny.
- Otherwise answer helpfully and concisely. Reply directly without thinking or reasoning
  steps.
"""


def _system_prompt() -> str:
    """Reuse the TUI's prompt without importing Textual at module load."""
    try:
        from tui.app import SYSTEM_PROMPT

        return SYSTEM_PROMPT
    except Exception:  # noqa: BLE001 - the server must work without the TUI
        return CHAT_SYSTEM_PROMPT


_SMALLTALK_RE = re.compile(
    r"^(hi|hey|hello|yo|sup|hiya)(\s+(gemma|there|bot|assistant|friend))?[\s!.,?]*$"
    r"|^(thanks|thank you|thx|ok|okay|"
    r"good (morning|afternoon|evening|night)|how are you|how's it going|"
    r"what'?s up|bye|goodbye)[\s!.,?]*$",
    re.IGNORECASE,
)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    gemma: "GemmaWebServer"


class GemmaWebServer:
    """Background HTTP server that serves the web UI and shares TUI state."""

    def __init__(
        self,
        settings: Settings,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        store: SessionStore | None = None,
        dist: Path | None = None,
        open_on_start: bool = False,
    ) -> None:
        self.settings = settings
        self.store = store or SessionStore()
        self.dist = Path(dist) if dist else _DIST
        self.host = host
        self.port = port
        self.open_on_start = open_on_start
        self.session_token = secrets.token_urlsafe(24)
        self._pair_token: str | None = None
        self._pair_expires = 0.0
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.agent = AgentRuntime(settings, self.store)
        from app.lifecycle import get_server_context_limit

        # The llama server's real n_ctx may be far below settings.context_limit;
        # use it everywhere (UI, compactor) so "ctx" numbers match reality.
        self.context_limit = get_server_context_limit(
            settings.base_url, settings.context_limit
        )
        settings.context_limit = self.context_limit
        self._chat_inflight = 0
        self._inflight_lock = threading.Lock()
        from core.titles import TitleManager

        self.titles = TitleManager(
            self.store,
            self.settings,
            lambda: bool(self.agent.active_runs())
            or bool(self._chat_inflight)
            or self.titles.busy(),
        )

    # ── lifecycle ───────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._httpd is not None and self._thread is not None and self._thread.is_alive()

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            return f"http://{self.host}:{self.port}"
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        if self.running:
            return self.base_url
        try:
            httpd = _Server((self.host, self.port), _Handler)
        except OSError:
            # Port taken: fall back to an ephemeral one so the TUI still works.
            httpd = _Server((self.host, 0), _Handler)
        httpd.gemma = self
        self._httpd = httpd
        self._stop.clear()
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="gemma-web", daemon=True
        )
        self._thread.start()
        self.titles.start_migration()
        if self.open_on_start:
            webbrowser.open(self.new_pairing_url())
        return self.base_url

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=5)
        self.titles.stop()
        self.agent.close()
        self._stop.set()

    def wait(self) -> None:
        """Block the calling thread until :meth:`stop` is called."""
        self._stop.wait()

    # ── pairing ─────────────────────────────────────────────────────────
    def new_pairing_url(self) -> str:
        self._pair_token = secrets.token_urlsafe(24)
        self._pair_expires = time.time() + PAIR_TTL
        return f"{self.base_url}/pair?token={self._pair_token}"

    def open_browser(self) -> str:
        url = self.new_pairing_url()
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - no browser is not fatal
            pass
        return url

    def _consume_pair_token(self, token: str) -> bool:
        if not token or self._pair_token is None:
            return False
        if time.time() > self._pair_expires:
            self._pair_token = None
            return False
        if not secrets.compare_digest(token, self._pair_token):
            return False
        # Links work once, like `opencode pair`.
        self._pair_token = None
        return True

    # ── chat ────────────────────────────────────────────────────────────
    def build_messages(self, session_id: str, context: str = "") -> list[dict[str, Any]]:
        raw = []
        for item in self.store.messages(session_id):
            message: dict[str, Any] = {
                "role": str(item.get("role")),
                "content": str(item.get("content") or ""),
            }
            metadata = item.get("metadata") or {}
            if metadata.get("attachments"):
                message["attachments"] = metadata["attachments"]
            raw.append(message)
        history, stale = recent_attachment_messages(raw)
        messages: list[dict[str, Any]] = [{"role": "system", "content": _system_prompt()}]
        extra = "\n".join([*stale, context.strip()]).strip()
        if extra:
            messages.append({"role": "system", "content": extra})
        messages.extend(history)
        return messages

    @staticmethod
    def _log_attachments(session_id: str, attachments: list[dict[str, Any]]) -> None:
        if not attachments:
            return
        from core.attachments import payload_bytes

        runlog.log(
            {
                "type": "attachment_request",
                "session_id": session_id,
                "count": len(attachments),
                "kinds": [item.get("kind") for item in attachments],
                "payload_bytes": payload_bytes(attachments),
                "tokens_est": sum(int(item.get("tokens_est") or 0) for item in attachments),
            }
        )

    def queue_title(self, session_id: str, reply: str) -> None:
        """Kick off a background title after an assistant reply is stored."""
        first, _ = self.store.first_exchange(session_id)
        if not first or not reply.strip():
            return
        messages = self.store.messages(session_id)
        exchange = sum(1 for item in messages if item.get("role") == "user")
        self.titles.submit(session_id, first, reply[:300], exchange=exchange)
        self.titles.maybe_refresh_drift(session_id, exchange)

    async def _pump_chat(self, handler: "_Handler", session_id: str, context: str = "") -> None:
        client = LlamaClient(self.settings)
        from core.attachments import multimodal_messages
        from core.providers import NativeToolProvider

        messages = self.build_messages(session_id, context)
        has_images = any(item.get("attachments") for item in messages)
        provider = NativeToolProvider(self.settings) if has_images else None
        started = time.monotonic()
        first_at: float | None = None
        text = ""
        tokens = 0
        prompt_tokens = 0
        try:
            if provider is not None:
                async for event in provider.stream(
                    multimodal_messages(messages), [], max_tokens=1024
                ):
                    if event.type == "text" and event.text:
                        if first_at is None:
                            first_at = time.monotonic()
                        text += event.text
                        tokens += 1
                        handler.emit({"type": "token", "content": event.text})
                    elif event.type == "usage":
                        prompt_tokens = int(event.usage.get("input_tokens") or 0)
                        tokens = max(tokens, int(event.usage.get("output_tokens") or tokens))
            else:
                async for chunk in client.stream_chat(messages, prefill=bool(context)):
                    if not chunk:
                        continue
                    if first_at is None:
                        first_at = time.monotonic()
                    text += chunk
                    tokens += 1
                    handler.emit({"type": "token", "content": chunk})
            elapsed = max(time.monotonic() - started, 0.001)
            ttft = (first_at - started) if first_at else elapsed
            if text.strip():
                self.store.add_message(session_id, "assistant", text)
                self.queue_title(session_id, text)
            handler.emit(
                {
                    "type": "done",
                    "elapsed": round(elapsed, 3),
                    "ttft": round(ttft, 3),
                    "tokens": tokens,
                    "prompt_tokens": prompt_tokens,
                }
            )
        except (BrokenPipeError, ConnectionResetError):
            if text.strip():
                self.store.add_message(session_id, "assistant", text)
        except Exception as exc:  # noqa: BLE001 - report model failures to the UI
            handler.emit({"type": "error", "message": str(exc)})
        finally:
            handler.emit_done()


class _Handler(BaseHTTPRequestHandler):
    server_version = "GemmaWeb/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def gemma(self) -> GemmaWebServer:
        return self.server.gemma  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Keep the TUI clean; set GEMMA_WEB_DEBUG=1 for access logs.
        import os

        if os.environ.get("GEMMA_WEB_DEBUG"):
            super().log_message(fmt, *args)

    # ── helpers ─────────────────────────────────────────────────────────
    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return None

    def _authorized(self) -> bool:
        cookie = self._cookie()
        return bool(cookie) and secrets.compare_digest(cookie, self.gemma.session_token)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        if self.path.startswith("/api/"):
            self._json({"error": "pairing required", "pair": "/pair"}, 401)
        else:
            self._html(_PAIR_PAGE, 403)
        return False

    def _set_pair_cookie(self) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={self.gemma.session_token}; Path=/; "
            f"Max-Age={COOKIE_TTL}; HttpOnly; SameSite=Lax",
        )
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── routing ─────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._json({"status": "ok", "app": "gemma"})
            return
        if path == "/pair":
            token = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
            if self.gemma._consume_pair_token(token):
                self._set_pair_cookie()
            else:
                self._html(_EXPIRED_PAGE, 403)
            return
        if not self._require_auth():
            return
        if path == "/api/info":
            self._json(self._info())
        elif path == "/api/logs":
            self._server_log()
        elif path == "/api/attachments/capabilities":
            from core.attachments import capabilities

            self._json(capabilities(self.gemma.settings.base_url))
        elif path == "/api/attachments/suggest":
            from core.attachments import suggest_paths

            query = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
            self._json({"paths": suggest_paths(query)})
        elif path == "/api/sessions":
            self._json({"sessions": self._sessions()})
        elif path == "/api/models":
            self._json(self._models())
        elif path == "/api/providers":
            self._json(self.gemma.agent.providers())
        elif path == "/api/commands":
            self._json({"commands": self._commands()})
        elif path == "/api/pair":
            self._json({"pair": self.gemma.new_pairing_url()})
        elif path == "/api/permissions/config":
            self._permission_config()
        elif path == "/api/mcp/status":
            self._json({"enabled": self.gemma.settings.mcp_enabled, "status": self.gemma.agent.service_status("mcp")})
        elif path == "/api/skills":
            self._json({"enabled": self.gemma.settings.skills_enabled, "summary": self.gemma.agent.service_status("skills")})
        elif path == "/api/lsp/status":
            self._json({"enabled": self.gemma.settings.lsp_enabled, "status": self.gemma.agent.service_status("lsp")})
        elif path == "/api/research/status":
            self._research_status()
        elif path.startswith("/api/sessions/"):
            session_id = unquote(path.split("/", 3)[3]).split("/")[0]
            self._session_detail(session_id)
        elif path.startswith("/api/"):
            self._json({"error": "not found"}, 404)
        else:
            self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._require_auth():
            return
        if path == "/api/sessions":
            session_id = self.gemma.store.create_session()
            self._json({"id": session_id}, 201)
        elif path == "/api/models":
            self._switch_model()
        elif path == "/api/providers":
            self._json(self.gemma.agent.providers())
        elif path == "/api/mode":
            self._set_mode()
        elif path == "/api/permissions/config":
            self._write_permission_config()
        elif path == "/api/provider_mode":
            self._set_provider_mode()
        elif path == "/api/context":
            self._set_context()
        elif path == "/api/agent/settings":
            self._agent_settings()
        elif path in ("/api/mcp", "/api/skills", "/api/lsp"):
            self._service_action(path.split("/")[-1])
        elif path == "/api/attachments/prepare":
            self._attachment_prepare()
        elif path == "/api/research/prepare":
            self._research_prepare()
        elif path == "/api/research/read":
            self._research_read()
        elif path == "/api/research/test":
            self._research_test()
        elif path == "/api/research/config":
            self._research_config()
        elif path == "/api/compact":
            self._compact()
        elif path == "/api/init":
            self._init_workspace()
        elif path == "/api/agent":
            self._agent()
        elif path == "/api/agent/abort":
            self._abort_agent()
        elif path.startswith("/api/permissions/"):
            self._permission()
        elif path == "/api/pair":
            self._json({"pair": self.gemma.new_pairing_url()})
        elif path == "/api/chat":
            self._chat()
        elif path.startswith("/api/sessions/") and path.endswith("/clear"):
            session_id = unquote(path.split("/")[3])
            self.gemma.store.clear_session(session_id)
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._require_auth():
            return
        if path.startswith("/api/sessions/"):
            session_id = unquote(path.split("/", 3)[3]).split("/")[0]
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "invalid json"}, 400)
                return
            title = str(body.get("title") or "").strip()
            if not title:
                self._json({"error": "title required"}, 400)
                return
            self.gemma.store.rename_session(session_id, title)
            self._json({"ok": True, "title": title[:60]})
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._require_auth():
            return
        if path.startswith("/api/sessions/"):
            session_id = unquote(path.split("/", 3)[3]).split("/")[0]
            self.gemma.store.delete_session(session_id)
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    # ── API ─────────────────────────────────────────────────────────────
    def _server_log(self) -> None:
        """Tail the server log for the in-TUI ``/logs`` panel."""
        log_path = server_log_path()
        lines: list[str] = []
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-200:]
        except OSError:
            pass
        self._json({"path": str(log_path), "text": "".join(lines)})

    def _info(self) -> dict[str, Any]:
        settings = self.gemma.settings
        try:
            active = self.gemma.agent.providers()["active"]
        except Exception:  # noqa: BLE001 - info must always answer
            active = {"provider": "llama.cpp", "model": settings.model}
        from core.attachments import capabilities

        caps = capabilities(settings.base_url)
        return {
            "app": "gemma",
            "vision": caps["vision"],
            "attachments": caps,
            "version": "0.1.0",
            "model": settings.model,
            "mode": "agent" if settings.agent_mode else "chat",
            "workspace": str(settings.workspace_dir),
            "provider": active["provider"],
            "provider_mode": settings.provider,
            "base_url": settings.base_url,
            "permission_mode": getattr(settings, "permission_mode", "auto"),
            "temperature": settings.temperature,
            "context_max": self.gemma.context_limit,
            "max_steps": getattr(settings, "agent_max_steps", 10),
            "dist": self.gemma.dist.is_dir(),
        }

    def _models(self) -> dict[str, Any]:
        """Current, server-loaded and local GGUF models, de-duplicated."""
        settings = self.gemma.settings
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(name: str, source: str) -> None:
            name = (name or "").strip()
            if name and name not in seen:
                seen.add(name)
                entries.append({"name": name, "source": source, "active": name == settings.model})

        add(settings.model, "current")
        try:
            from app.lifecycle import get_loaded_model

            loaded = get_loaded_model(settings.base_url)
            if loaded:
                add(loaded, "loaded")
        except Exception:  # noqa: BLE001 - the model list must always render
            pass
        try:
            from models.registry import get_available_models

            for name in get_available_models().keys():
                add(name, "local")
        except Exception:  # noqa: BLE001
            pass
        return {"active": settings.model, "models": entries}

    def _switch_model(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        provider_id = str(body.get("provider") or "").strip()
        name = str(body.get("name") or body.get("model") or "").strip()
        if not name:
            self._json({"error": "name required"}, 400)
            return
        if provider_id:
            ok, error = self.gemma.agent.switch_model(provider_id, name)
            if not ok:
                self._json({"error": error}, 400)
                return
        else:
            self.gemma.settings.save_model(name)
        self._json(
            {
                "ok": True,
                "active": name,
                "provider": self.gemma.agent.providers()["active"],
            }
        )

    def _set_mode(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        mode = str(body.get("mode") or "").strip().lower()
        if mode not in ("agent", "chat"):
            self._json({"error": "mode must be 'agent' or 'chat'"}, 400)
            return
        self.gemma.settings.save_agent_mode(mode == "agent")
        self._json({"ok": True, "mode": mode})

    def _agent(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        message = str(body.get("message") or "").strip()
        if not message:
            self._json({"error": "message required"}, 400)
            return
        session_id = str(body.get("session_id") or "").strip()
        if not session_id or self.gemma.store.get_session(session_id) is None:
            session_id = self.gemma.store.create_session()
        context = str(body.get("context") or "").strip()

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.emit({"type": "start", "session_id": session_id})
        try:
            attachments = body.get("attachments") or []
            if attachments:
                from core.attachments import relocate_attachment

                attachments = [relocate_attachment(dict(a), session_id) for a in attachments]
                self.gemma._log_attachments(session_id, attachments)
            run = self.gemma.agent.start(session_id, message, context=context, attachments=attachments)
            self.emit({"type": "run", "run_id": run.run_id, "session_id": session_id})
            self._drain(run)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _drain(self, run: Any) -> None:
        """Forward agent events until the run closes, with SSE keep-alives."""
        while True:
            try:
                event = run.events.get(timeout=15)
            except queue.Empty:
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # The client is gone; stop the run instead of letting it
                    # wait on a permission prompt nobody will answer.
                    self.gemma.agent.abort(run.run_id)
                    return
                continue
            if event is None:
                break
            if event.get("type") == "complete" and isinstance(event.get("content"), str):
                self.gemma.queue_title(run.session_id, event["content"])
            try:
                self.emit(event)
            except (BrokenPipeError, ConnectionResetError):
                self.gemma.agent.abort(run.run_id)
                return
        self.emit_done()

    def _abort_agent(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        run_id = str(body.get("run_id") or "").strip()
        ok = self.gemma.agent.abort(run_id) if run_id else False
        self._json({"ok": ok})

    def _permission(self) -> None:
        request_id = unquote(urlparse(self.path).path.split("/", 3)[3]).split("/")[0]
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        decision = str(body.get("decision") or "deny").strip().lower()
        ok = self.gemma.agent.resolve_permission(request_id, decision)
        if not ok:
            self._json({"error": "unknown or expired request"}, 404)
            return
        self._json({"ok": True, "decision": decision})

    # ── agent settings: permissions, provider mode, context limit, agent ──

    def _permission_config_data(self) -> dict[str, Any]:
        settings = self.gemma.settings
        return {
            "mode": getattr(settings, "permission_mode", "auto"),
            "rules": list(getattr(settings, "permission_rules", []) or []),
        }

    def _permission_config(self) -> None:
        self._json(self._permission_config_data())

    def _write_permission_config(self) -> None:
        settings = self.gemma.settings
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return

        if body.get("reset"):
            settings.save_permission_rules([])
            self._json(self._permission_config_data())
            return

        mode = body.get("mode")
        if mode is not None:
            try:
                settings.save_permission_mode(str(mode))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return

        add = body.get("add")
        if add is not None:
            effect = str(body.get("effect") or "").strip().lower()
            action = str(body.get("action") or "").strip() or "*"
            resource = str(body.get("resource") or "").strip() or "*"
            if add:
                # ``add`` may be a full "action:resource:effect" string.
                parts = str(add).split(":")
                if len(parts) >= 3:
                    action, resource, effect = parts[0], ":".join(parts[1:-1]), parts[-1]
            if effect not in ("allow", "deny", "ask"):
                self._json({"error": "effect must be allow, deny, or ask"}, 400)
                return
            rules = list(getattr(settings, "permission_rules", []) or [])
            rules.append(f"{action}:{resource}:{effect}")
            settings.save_permission_rules(rules)

        self._json(self._permission_config_data())

    def _set_provider_mode(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        try:
            self.gemma.settings.save_provider(str(body.get("mode") or ""))
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        self._json({"ok": True, "provider_mode": self.gemma.settings.provider})

    def _set_context(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        try:
            limit = int(body.get("limit"))
        except (TypeError, ValueError):
            self._json({"error": "limit must be a number"}, 400)
            return
        self.gemma.settings.save_context_limit(limit)
        self._json({"ok": True, "context_max": self.gemma.settings.context_limit})

    def _agent_settings(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        settings = self.gemma.settings
        workspace = body.get("workspace")
        if workspace is not None:
            settings.save_workspace_dir(str(workspace))
        steps = body.get("max_steps")
        if steps is not None:
            try:
                settings.save_agent_max_steps(int(steps))
            except (TypeError, ValueError):
                self._json({"error": "max_steps must be a number"}, 400)
                return
        self._json(
            {
                "ok": True,
                "workspace": str(settings.workspace_dir),
                "max_steps": settings.agent_max_steps,
            }
        )

    def _service_action(self, service: str) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        action = str(body.get("action") or "status").strip().lower()
        allowed = {
            "mcp": ("start", "reload", "stop", "status"),
            "skills": ("list", "reload"),
            "lsp": ("status", "stop"),
        }
        if action not in allowed.get(service, ()):  # unknown service or action
            self._json({"error": f"action must be one of {allowed.get(service, ())}"}, 400)
            return
        try:
            if service == "mcp" and action == "status":
                status = self.gemma.agent.service_status("mcp")
            else:
                future = self.gemma.agent._submit(
                    self.gemma.agent.mcp_action(action)
                    if service == "mcp"
                    else self.gemma.agent.lsp_action(action)
                    if service == "lsp"
                    else self.gemma.agent.skills_action(action)
                )
                status = future.result(timeout=60)
        except Exception as exc:  # noqa: BLE001 - report the failure to the client
            self._json({"error": str(exc)}, 500)
            return
        self._json({"ok": True, "service": service, "action": action, "status": status})

    # ── attachments ──────────────────────────────────────────────────────
    def _attachment_prepare(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        raw_path = str(body.get("path") or "").strip()
        session_id = str(body.get("session_id") or "new").strip()
        if not raw_path:
            self._json({"error": "path required"}, 400)
            return
        from core.attachments import AttachmentError, capabilities, prepare_attachment

        if Path(raw_path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            caps = capabilities(self.gemma.settings.base_url)
            if not caps["vision"]:
                self._json(
                    {
                        "error": "Image attachments need llama.cpp started with --mmproj. "
                        "Restart the server with: --mmproj models/gemma-4-E4B-it-mmproj-BF16.gguf",
                        "vision": False,
                    },
                    400,
                )
                return
        try:
            descriptor = prepare_attachment(session_id, raw_path)
        except AttachmentError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - user-facing error
            self._json({"error": f"Could not attach file: {exc}"}, 400)
            return
        self._json({"ok": True, "attachment": descriptor})

    # ── web research (SearXNG) ──────────────────────────────────────────

    @staticmethod
    def _result_payload(item: dict[str, Any]) -> dict[str, str]:
        return {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "engine": str(item.get("engine") or ""),
            "date": str(item.get("date") or ""),
        }

    def _research_prepare(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        message = str(body.get("message") or "").strip()
        if not message:
            self._json({"error": "message required"}, 400)
            return
        if _SMALLTALK_RE.match(message):
            self._json({"search": False, "reason": "smalltalk"})
            return
        history_raw = body.get("history") or []
        history: list[str] = []
        for item in history_raw[-6:]:
            if isinstance(item, dict):
                content = str(item.get("content") or "").strip()
            else:
                content = str(item or "").strip()
            if content:
                history.append(content[:500])
        deep_read = bool(body.get("deep_read", True))

        async def run() -> dict[str, Any]:
            from core.orchestrator import decide_search
            from core.research import format_context, intent_of, query_entity, rewrite_query, web_search

            settings = self.gemma.settings
            client = LlamaClient(settings)
            needs, queries = await decide_search(message, client)
            if not needs:
                return {"search": False}
            rewritten = await rewrite_query(
                queries[0] if queries else message, history, client, settings.search_region
            )
            candidates = [rewritten] + [
                query for query in queries + [message] if query != rewritten
            ]
            intent = intent_of(rewritten)
            started = time.monotonic()
            for query in candidates:
                try:
                    results = await web_search(
                        query, searxng_url=settings.searxng_url,
                        deep_read=deep_read, intent=intent,
                    )
                except Exception:  # noqa: BLE001 - fall through to the next query
                    results = []
                if results:
                    response: dict[str, Any] = {
                        "search": True,
                        "query": query,
                        "rewritten": rewritten,
                        "intent": intent,
                        "results": [self._result_payload(item) for item in results],
                        "elapsed": round(time.monotonic() - started, 3),
                    }
                    # The one-shot callers (classic flow, web UI) also want the
                    # deep-read context; the TS client runs the second phase
                    # itself so it can show a "reading…" status in between.
                    if deep_read:
                        response["context"] = format_context(results)
                    return response
            # Every query came back empty: tell the client why, so it can show
            # a clear error instead of silently answering from memory.
            from core.research import check_searxng_health

            healthy, detail = await check_searxng_health(settings.searxng_url)
            return {
                "search": True,
                "query": rewritten,
                "rewritten": rewritten,
                "intent": intent,
                "results": [],
                "entity": query_entity(rewritten),
                "healthy": healthy,
                "detail": detail,
            }

        self._json(asyncio.run(run()))

    def _research_read(self) -> None:
        """Second phase for the TS client: deep-read the top result pages.

        Body: {query, results: [{title,url,engine,date}]}. Fetches page text in
        parallel (capped, timed) and returns the model-ready context plus the
        enriched results so the client can show sources with dates.
        """
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        query = str(body.get("query") or "").strip()
        raw_results = body.get("results") or []
        if not isinstance(raw_results, list) or not raw_results:
            self._json({"ok": False, "error": "results required", "results": []})
            return

        results = [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "engine": str(item.get("engine") or ""),
                "date": str(item.get("date") or ""),
                "snippet": str(item.get("snippet") or ""),
            }
            for item in raw_results[:6]
            if isinstance(item, dict) and str(item.get("url") or "")
        ]
        if not results:
            self._json({"ok": False, "error": "no usable results", "results": []})
            return

        async def run() -> dict[str, Any]:
            from core.research import (
                _SEARCH_TIMEOUT, _USER_AGENTS, _enrich, format_context, intent_of,
                relevant_results,
            )

            async with httpx.AsyncClient(
                timeout=_SEARCH_TIMEOUT + 2, follow_redirects=True
            ) as client:
                enriched = await _enrich(results, client, _USER_AGENTS[0], deep_read=True)

            # Only sources whose page text was actually fetched are fed to the
            # model and shown in the sources block.
            used = [
                item for item in relevant_results(enriched, query, intent_of(query))
                if item.get("excerpt")
            ]
            if not used:
                return {"ok": False, "error": "no page text could be fetched", "results": []}
            return {
                "ok": True,
                "query": query,
                "context": format_context(used),
                "results": [
                    self._result_payload(item) | {"excerpt": str(item.get("excerpt") or "")}
                    for item in used
                ],
            }

        self._json(asyncio.run(run()))

    def _research_status(self) -> None:
        from core.research import check_searxng_health

        settings = self.gemma.settings
        healthy, detail = asyncio.run(check_searxng_health(settings.searxng_url))
        self._json(
            {
                "healthy": healthy,
                "detail": detail,
                "url": settings.searxng_url,
                "deep_read": settings.searxng_deep_read,
            }
        )

    def _research_test(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        query = str(body.get("query") or "").strip() or "PyTorch latest version"
        from core.research import web_search

        try:
            results = asyncio.run(
                web_search(query, top_n=3, searxng_url=self.gemma.settings.searxng_url)
            )
        except Exception as exc:  # noqa: BLE001 - report engine failures
            self._json({"ok": False, "query": query, "error": str(exc), "results": []})
            return
        self._json({"ok": bool(results), "query": query, "results": results[:3]})

    def _research_config(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        url = str(body.get("url") or "").strip()
        if not url:
            self._json({"error": "url required"}, 400)
            return
        from core.research import check_searxng_health

        settings = self.gemma.settings
        settings.save_searxng_url(url)
        healthy, detail = asyncio.run(check_searxng_health(settings.searxng_url))
        self._json(
            {
                "ok": True,
                "url": settings.searxng_url,
                "healthy": healthy,
                "detail": detail,
            }
        )

    # ── compaction ──────────────────────────────────────────────────────

    def _compact(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        session_id = str(body.get("session_id") or "").strip()
        if not session_id or self.gemma.store.get_session(session_id) is None:
            self._json({"error": "session not found"}, 404)
            return
        instructions = str(body.get("instructions") or "").strip()

        from core.client import LlamaClient
        from core.compaction import Compactor

        settings = self.gemma.settings
        compactor = Compactor(
            LlamaClient(settings),
            context_limit=settings.context_limit,
            threshold=getattr(settings, "compact_threshold", 0.8),
            keep_tokens=getattr(settings, "compact_keep_tokens", 4_000),
        )

        async def run() -> dict[str, Any]:
            result = await compactor.compact(
                self.gemma.store.context_messages(session_id),
                reason="manual",
                instructions=instructions,
            )
            if result is None:
                return {"ok": False, "reason": "nothing to compact yet"}
            self.gemma.store.add_message(
                session_id,
                "system",
                "<conversation-checkpoint>\n" + result.summary + "\n</conversation-checkpoint>",
                metadata={"kind": "checkpoint", "reason": "manual"},
            )
            self.gemma.store.set_summary(session_id, result.summary)
            return {
                "ok": True,
                "removed": result.removed,
                "before_tokens": result.before_tokens,
                "after_tokens": result.after_tokens,
                "summary": result.summary,
            }

        self._json(asyncio.run(run()))

    # ── workspace bootstrap ─────────────────────────────────────────────

    def _init_workspace(self) -> None:
        target = (Path(self.gemma.settings.workspace_dir) / "AGENTS.md").resolve()
        if target.exists():
            self._json({"ok": False, "path": str(target), "reason": "exists"})
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# Project Instructions\n\n"
            "## Overview\n"
            "Describe the project and its purpose.\n\n"
            "## Commands\n"
            "- Build: `...`\n"
            "- Test: `...`\n"
            "- Lint: `...`\n\n"
            "## Conventions\n"
            "- Language and style\n"
            "- Testing expectations\n"
            "- Files that must not be edited directly\n",
            encoding="utf-8",
        )
        self._json({"ok": True, "path": str(target)})

    def _commands(self) -> list[dict[str, str]]:
        try:
            from core.commands import COMMAND_DEFS

            return [{"name": name, "description": description} for name, description in COMMAND_DEFS]
        except Exception:  # noqa: BLE001 - the client ships its own fallback
            return []

    def _sessions(self) -> list[dict[str, Any]]:
        rows = self.gemma.store.list_sessions(include_empty=True)
        return [
            {
                "id": row.id,
                "title": row.title,
                "updated_at": _iso(row.updated_at),
                "message_count": self.gemma.store.count_messages(row.id),
            }
            for row in rows
        ]

    def _session_detail(self, session_id: str) -> None:
        session = self.gemma.store.get_session(session_id)
        if session is None:
            self._json({"error": "session not found"}, 404)
            return
        messages = [
            {
                "role": item.get("role"),
                "content": item.get("content"),
                "created_at": _iso(item.get("created_at")),
                "tool_call_id": item.get("tool_call_id"),
                "name": item.get("name"),
                "metadata": item.get("metadata") or {},
            }
            for item in self.gemma.store.messages(session_id)
        ]
        self._json(
            {
                "id": session.id,
                "title": session.title,
                "updated_at": _iso(session.updated_at),
                "messages": messages,
            }
        )

    # ── chat stream ─────────────────────────────────────────────────────
    def _chat(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid json"}, 400)
            return
        message = str(body.get("message") or "").strip()
        if not message:
            self._json({"error": "message required"}, 400)
            return
        session_id = str(body.get("session_id") or "").strip()
        if not session_id or self.gemma.store.get_session(session_id) is None:
            session_id = self.gemma.store.create_session()
        attachments = body.get("attachments") or []
        if attachments:
            from core.attachments import relocate_attachment

            attachments = [relocate_attachment(dict(a), session_id) for a in attachments]
        self.gemma._log_attachments(session_id, attachments)
        self.gemma.store.add_message(
            session_id,
            "user",
            message,
            metadata={"attachments": attachments},
        )
        context = str(body.get("context") or "").strip()
        with self.gemma._inflight_lock:
            self.gemma._chat_inflight += 1

        # The stream has no Content-Length, so the response ends when the
        # connection closes: keep-alive would leave the browser waiting.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.emit({"type": "start", "session_id": session_id})
        try:
            asyncio.run(self.gemma._pump_chat(self, session_id, context))
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.gemma._inflight_lock:
                self.gemma._chat_inflight -= 1

    def emit(self, payload: dict[str, Any]) -> None:
        data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(data)
        self.wfile.flush()

    def emit_done(self) -> None:
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── static files ────────────────────────────────────────────────────
    def _static(self, path: str) -> None:
        root = self.gemma.dist
        if not (root / "index.html").is_file():
            self._html(_BUILD_PAGE, 503)
            return

        relative = unquote(path).lstrip("/") or "index.html"
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self._json({"error": "forbidden"}, 403)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            if "." not in Path(relative).name:
                target = root / "index.html"
            else:
                self._json({"error": "not found"}, 404)
                return

        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".svg":
            content_type = "image/svg+xml"
        elif target.suffix == ".html":
            content_type = "text/html; charset=utf-8"

        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if relative.startswith("assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


_PAIR_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Gemma · pair</title><style>
body{background:#121212;color:#e5e7eb;font:14px/1.6 ui-sans-serif,system-ui;display:grid;
place-items:center;height:100vh;margin:0}
.card{max-width:460px;padding:28px;border:1px solid #2a2a2a;border-radius:14px;background:#1a1a1a}
h1{font-size:16px;margin:0 0 8px}code{background:#111;padding:2px 6px;border-radius:6px;color:#22d3ee}
p{color:#9ca3af;margin:8px 0 0}</style></head><body><div class="card">
<h1>Pair this browser</h1>
<p>Open the one-time link printed by the Gemma TUI (<code>/web</code> or
<code>Ctrl+W</code>) to sign in.</p></div></body></html>"""

_EXPIRED_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Gemma · link expired</title><style>
body{background:#121212;color:#e5e7eb;font:14px/1.6 ui-sans-serif,system-ui;display:grid;
place-items:center;height:100vh;margin:0}
.card{max-width:460px;padding:28px;border:1px solid #2a2a2a;border-radius:14px;background:#1a1a1a}
h1{font-size:16px;margin:0 0 8px}code{background:#111;padding:2px 6px;border-radius:6px;color:#22d3ee}
p{color:#9ca3af;margin:8px 0 0}</style></head><body><div class="card">
<h1>Pairing link expired</h1>
<p>Links work once and expire after five minutes. Run <code>/web</code> in the
Gemma TUI to get a fresh one.</p></div></body></html>"""

_BUILD_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Gemma · web UI not built</title><style>
body{background:#121212;color:#e5e7eb;font:14px/1.6 ui-sans-serif,system-ui;display:grid;
place-items:center;height:100vh;margin:0}
.card{max-width:520px;padding:28px;border:1px solid #2a2a2a;border-radius:14px;background:#1a1a1a}
h1{font-size:16px;margin:0 0 8px}pre{background:#111;padding:12px;border-radius:8px;color:#22d3ee}
p{color:#9ca3af;margin:8px 0 0}</style></head><body><div class="card">
<h1>Web UI is not built yet</h1>
<p>Build the frontend once, then reload this page:</p>
<pre>cd web
npm install
npm run build</pre></div></body></html>"""
