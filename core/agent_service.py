"""Shared agent runtime for the HTTP API and terminal clients.

The web server handles every request on its own thread, but MCP and LSP clients
are bound to the asyncio loop that created them. So the agent runs on one
dedicated background loop, and each request gets a thread-safe queue of events
that the SSE handler drains.

Permission prompts travel the same way: the agent emits ``permission_request``
into the run's queue, then awaits a decision that the client posts back to
``/api/permissions/<id>``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import queue
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

from config.settings import Settings
from core.agent import AgentOrchestrator
from core.permissions import PermissionManager, PermissionRequest, PermissionRule, parse_rule
from core.tooling import build_default_registry
from sessions.store import SessionStore


@dataclass
class AgentRun:
    """One agent turn: a queue of events plus the ability to cancel it."""

    run_id: str
    session_id: str
    events: "queue.Queue[dict[str, Any] | None]" = field(default_factory=queue.Queue)
    future: Any = None
    aborted: bool = False

    def emit(self, payload: dict[str, Any]) -> None:
        self.events.put(payload)

    def close(self) -> None:
        self.events.put(None)


_CURRENT_RUN: contextvars.ContextVar[AgentRun | None] = contextvars.ContextVar(
    "gemma_agent_run", default=None
)


def _json_safe(value: Any) -> Any:
    """Agent payloads are mostly plain data; coerce the rest for SSE."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class AgentRuntime:
    """Owns the agent loop, the cached tool registry and pending approvals."""

    def __init__(self, settings: Settings, store: SessionStore) -> None:
        self.settings = settings
        self.store = store
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve_loop, name="gemma-agent", daemon=True)
        self._started = threading.Event()
        self._registry: Any = None
        self._skills: Any = None
        self._mcp: Any = None
        self._lsp: Any = None
        self._mcp_started = False
        self._permissions: dict[str, asyncio.Future[str]] = {}
        self._runs: dict[str, AgentRun] = {}
        self._lock = threading.Lock()
        self._thread.start()
        self._started.wait(5)

    # ── loop plumbing ───────────────────────────────────────────────────
    def _serve_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def close(self) -> None:
        for run in list(self._runs.values()):
            run.aborted = True
            self._cancel(run)
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass
        self._thread.join(timeout=5)

    def _submit(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _cancel(self, run: AgentRun) -> None:
        future = run.future
        if future is None:
            return
        try:
            self._loop.call_soon_threadsafe(future.cancel)
        except RuntimeError:
            pass

    # ── integrations ────────────────────────────────────────────────────
    async def _ensure_integrations(self) -> Any:
        if self._registry is not None:
            return self._registry

        from core.lsp import LSPManager, lsp_tools
        from core.mcp import MCPManager
        from core.skills import SkillsManager

        registry = build_default_registry()

        if getattr(self.settings, "skills_enabled", True):
            try:
                self._skills = SkillsManager(
                    self.settings.workspace_dir,
                    getattr(self.settings, "skills_dirs", []),
                )
                for spec in self._skills.tool_specs():
                    if registry.get(spec.name) is None:
                        registry.register(spec)
            except Exception:  # noqa: BLE001 - skills are optional
                self._skills = None

        if getattr(self.settings, "lsp_enabled", True):
            try:
                self._lsp = LSPManager(self.settings.workspace_dir)
                for spec in lsp_tools(self._lsp):
                    if registry.get(spec.name) is None:
                        registry.register(spec)
            except Exception:  # noqa: BLE001 - LSP is optional
                self._lsp = None

        if getattr(self.settings, "mcp_enabled", True):
            try:
                self._mcp = MCPManager(
                    self.settings.workspace_dir,
                    getattr(self.settings, "mcp_config", ".gemma/mcp.json"),
                )
                if not self._mcp_started:
                    try:
                        await self._mcp.start()
                    finally:
                        # Never retry a slow connect on every message.
                        self._mcp_started = True
                self._mcp.register_tools(registry)
            except Exception:  # noqa: BLE001 - MCP is optional
                pass

        self._registry = registry
        return registry

    def reload_integrations(self) -> None:
        self._registry = None
        self._mcp_started = False
        if self._skills is not None:
            try:
                self._skills.reload()
            except Exception:  # noqa: BLE001
                pass

    # ── service status (MCP / skills / LSP) ─────────────────────────────

    def service_status(self, service: str) -> str:
        """Sync status snapshot; safe to call from the HTTP thread."""
        if service == "mcp":
            if not getattr(self.settings, "mcp_enabled", True):
                return "MCP is disabled in settings."
            if self._mcp is None:
                return "MCP is not started yet (it starts with the first agent run)."
            return self._mcp.status()
        if service == "skills":
            if not getattr(self.settings, "skills_enabled", True):
                return "Skills are disabled in settings."
            if self._skills is None:
                return "Skills are not loaded yet (they load with the first agent run)."
            return self._skills.summary() or "No skills installed."
        if service == "lsp":
            if not getattr(self.settings, "lsp_enabled", True):
                return "LSP is disabled in settings."
            if self._lsp is None:
                return "LSP is not started yet (it starts with the first agent run)."
            return self._lsp.status()
        return f"Unknown service {service}."

    async def mcp_action(self, action: str) -> str:
        """start|reload|stop, mirroring the classic TUI semantics."""
        if not getattr(self.settings, "mcp_enabled", True):
            return "MCP is disabled in settings."
        if action == "stop":
            if self._mcp is not None:
                await self._mcp.stop()
            self._mcp = None
            self._mcp_started = False
            self._registry = None
            return "MCP stopped."
        from core.mcp import MCPManager

        if self._mcp is None or action == "reload":
            if self._mcp is not None:
                await self._mcp.stop()
            self._mcp = MCPManager(
                self.settings.workspace_dir,
                getattr(self.settings, "mcp_config", ".gemma/mcp.json"),
            )
        if not self._mcp_started or action == "reload":
            try:
                await self._mcp.start()
            except Exception as exc:  # noqa: BLE001 - report, don't crash the loop
                self._mcp_started = True
                return f"MCP start failed: {exc}"
            self._mcp_started = True
            self._registry = None
        return self._mcp.status()

    async def lsp_action(self, action: str) -> str:
        """status|stop."""
        if not getattr(self.settings, "lsp_enabled", True):
            return "LSP is disabled in settings."
        if action == "stop":
            if self._lsp is not None:
                await self._lsp.stop()
            self._lsp = None
            self._registry = None
            return "LSP servers stopped."
        return self.service_status("lsp")

    async def skills_action(self, action: str) -> str:
        """list (status snapshot) | reload."""
        if action == "reload":
            self.reload_integrations()
        return self.service_status("skills")

    # ── permissions ─────────────────────────────────────────────────────
    async def _approve(self, request: PermissionRequest) -> str:
        run = _CURRENT_RUN.get()
        request_id = secrets.token_urlsafe(9)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        with self._lock:
            self._permissions[request_id] = future
        if run is not None:
            run.emit(
                {
                    "type": "permission_request",
                    "id": request_id,
                    "run_id": run.run_id,
                    "session_id": request.session_id,
                    "action": request.action,
                    "tool": request.tool,
                    "resources": list(request.resources),
                    "summary": request.summary(),
                    "reason": request.reason,
                    "args": _json_safe(request.args),
                }
            )
        try:
            # A generous ceiling: the user may step away mid-run.
            return await asyncio.wait_for(future, timeout=900)
        except asyncio.TimeoutError:
            return "deny"
        finally:
            with self._lock:
                self._permissions.pop(request_id, None)

    def resolve_permission(self, request_id: str, decision: str) -> bool:
        decision = decision if decision in ("allow", "always", "deny") else "deny"
        with self._lock:
            future = self._permissions.get(request_id)
        if future is None:
            return False

        def _set() -> None:
            if not future.done():
                future.set_result(decision)

        try:
            self._loop.call_soon_threadsafe(_set)
        except RuntimeError:
            return False
        return True

    def pending_permissions(self) -> list[str]:
        with self._lock:
            return list(self._permissions)

    # ── runs ────────────────────────────────────────────────────────────
    def _permission_manager(self, session_id: str) -> PermissionManager:
        rules: list[PermissionRule] = []
        for raw in getattr(self.settings, "permission_rules", []) or []:
            try:
                rules.append(parse_rule(raw))
            except ValueError:
                continue
        return PermissionManager(
            mode=getattr(self.settings, "permission_mode", "auto"),
            rules=rules,
            approval=self._approve,
            session_id=session_id,
        )

    def start(self, session_id: str, message: str, context: str = "") -> AgentRun:
        """Persist the user turn and launch an agent run; drain ``run.events``."""
        if message.strip():
            self.store.add_message(session_id, "user", message)
        run = AgentRun(run_id=secrets.token_urlsafe(9), session_id=session_id)
        with self._lock:
            self._runs[run.run_id] = run
        run.future = self._submit(self._execute(run, context))
        return run

    async def _execute(self, run: AgentRun, context: str = "") -> None:
        token = _CURRENT_RUN.set(run)
        try:
            registry = await self._ensure_integrations()
            orchestrator = AgentOrchestrator(
                client=None,
                settings=self.settings,
                max_steps=self.settings.agent_max_steps,
                registry=registry,
                permission=self._permission_manager(run.session_id),
                approval=self._approve,
                store=self.store,
                session_id=run.session_id,
                skills=self._skills,
                provider_mode=getattr(self.settings, "provider", "auto"),
                extra_context=context,
            )
            messages = self.store.context_messages(run.session_id)
            # Prime the model's prompt cache with the (large) agent prefix so
            # the first streaming step doesn't re-prefill it from scratch.
            warmup = getattr(orchestrator, "warmup", None)
            if callable(warmup):
                try:
                    await warmup()
                except Exception:  # noqa: BLE001 - warmup must never break a run
                    pass
            async for event in orchestrator.run(messages):
                if run.aborted:
                    break
                run.emit(
                    {
                        "type": event.type,
                        "session_id": run.session_id,
                        "run_id": run.run_id,
                        "content": _json_safe(event.content),
                    }
                )
            if run.aborted:
                run.emit({"type": "aborted", "run_id": run.run_id})
        except asyncio.CancelledError:
            run.emit({"type": "aborted", "run_id": run.run_id})
            raise
        except Exception as exc:  # noqa: BLE001 - surface failures to the client
            run.emit(
                {
                    "type": "error",
                    "run_id": run.run_id,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            _CURRENT_RUN.reset(token)
            run.close()
            with self._lock:
                self._runs.pop(run.run_id, None)

    def abort(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            return False
        run.aborted = True
        self._cancel(run)
        return True

    def active_runs(self) -> list[str]:
        with self._lock:
            return list(self._runs)

    # ── models ──────────────────────────────────────────────────────────
    def providers(self) -> dict[str, Any]:
        from core.provider_catalog import catalog

        return catalog(self.settings)

    def switch_model(self, provider_id: str, model: str) -> tuple[bool, str]:
        from core.provider_catalog import switch

        return switch(self.settings, provider_id, model)


__all__ = ["AgentRun", "AgentRuntime"]
