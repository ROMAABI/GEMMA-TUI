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
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from config.settings import Settings
from core.agent_service import AgentRuntime
from core.client import LlamaClient
from sessions.store import SessionStore

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


def _system_prompt() -> str:
    """Reuse the TUI's prompt without importing Textual at module load."""
    try:
        from tui.app import SYSTEM_PROMPT

        return SYSTEM_PROMPT
    except Exception:  # noqa: BLE001 - the server must work without the TUI
        return "You are Gemma, a helpful local AI assistant."


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
    def build_messages(self, session_id: str, context: str = "") -> list[dict[str, str]]:
        history = [
            {"role": str(item.get("role")), "content": str(item.get("content") or "")}
            for item in self.store.messages(session_id)
        ]
        messages = [{"role": "system", "content": _system_prompt()}] + history
        if context.strip():
            # Web research context rides in as a system message, like the TUI did.
            messages.insert(1, {"role": "system", "content": context})
        return messages

    async def _pump_chat(self, handler: "_Handler", session_id: str, context: str = "") -> None:
        client = LlamaClient(self.settings)
        started = time.monotonic()
        first_at: float | None = None
        text = ""
        tokens = 0
        try:
            async for chunk in client.stream_chat(self.build_messages(session_id, context)):
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
            handler.emit(
                {
                    "type": "done",
                    "elapsed": round(elapsed, 3),
                    "ttft": round(ttft, 3),
                    "tokens": tokens,
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
        elif path == "/api/research/prepare":
            self._research_prepare()
        elif path == "/api/research/test":
            self._research_test()
        elif path == "/api/research/config":
            self._research_config()
        elif path == "/api/compact":
            self._compact()
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
    def _info(self) -> dict[str, Any]:
        settings = self.gemma.settings
        try:
            active = self.gemma.agent.providers()["active"]
        except Exception:  # noqa: BLE001 - info must always answer
            active = {"provider": "llama.cpp", "model": settings.model}
        return {
            "app": "gemma",
            "version": "0.1.0",
            "model": settings.model,
            "mode": "agent" if settings.agent_mode else "chat",
            "workspace": str(settings.workspace_dir),
            "provider": active["provider"],
            "provider_mode": settings.provider,
            "base_url": settings.base_url,
            "permission_mode": getattr(settings, "permission_mode", "auto"),
            "temperature": settings.temperature,
            "context_max": settings.context_limit,
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

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.emit({"type": "start", "session_id": session_id})
        try:
            run = self.gemma.agent.start(session_id, message)
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

    # ── web research (SearXNG) ──────────────────────────────────────────

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

        async def run() -> dict[str, Any]:
            from core.orchestrator import decide_search
            from core.research import search_context

            settings = self.gemma.settings
            client = LlamaClient(settings)
            needs, queries = await decide_search(message, client)
            if not needs:
                return {"search": False}
            started = time.monotonic()
            for query in queries:
                try:
                    context = await search_context(
                        query,
                        searxng_url=settings.searxng_url,
                        deep_read=settings.searxng_deep_read,
                    )
                except Exception:  # noqa: BLE001 - fall through to the next query
                    context = ""
                if context:
                    return {
                        "search": True,
                        "query": query,
                        "context": context,
                        "elapsed": round(time.monotonic() - started, 3),
                    }
            return {"search": True, "query": queries[0] if queries else "", "context": ""}

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
        self.gemma.store.add_message(session_id, "user", message)
        context = str(body.get("context") or "").strip()

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
