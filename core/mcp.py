"""Minimal MCP (Model Context Protocol) stdio client and tool registration.

The client speaks newline-delimited JSON-RPC over a child process's stdin/stdout,
which is the transport used by the overwhelming majority of MCP servers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.tooling.registry import ToolRegistry
from core.tooling.types import ToolContext, ToolResult, ToolSpec, object_schema

DEFAULT_TIMEOUT = 30.0
INIT_TIMEOUT = 20.0


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_") or "tool"


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    disabled: bool = False


@dataclass
class MCPTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self.stderr_tail: list[str] = []
        self.initialized = False

    async def start(self) -> None:
        if self.process is not None:
            return
        command = self.config.command
        resolved = shutil.which(command) or command
        env = os.environ.copy()
        env.update(self.config.env)
        cwd = self.config.cwd
        if cwd:
            cwd = str(Path(cwd).expanduser())
        self.process = await asyncio.create_subprocess_exec(
            resolved,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gemma-tui", "version": "0.1.0"},
            },
            timeout=INIT_TIMEOUT,
        )
        if "error" in result:
            raise RuntimeError(f"MCP initialize failed: {result['error']}")
        await self.notify("notifications/initialized", {})
        self.initialized = True

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self.stderr_tail.append(text)
            self.stderr_tail = self.stderr_tail[-50:]

    async def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if "id" in message and ("result" in message or "error" in message):
                future = self._pending.pop(int(message["id"]), None)
                if future is not None and not future.done():
                    future.set_result(message)
        # Fail all pending requests when the process exits.
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("MCP server exited"))
        self._pending.clear()

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("MCP server is not running")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def list_tools(self) -> list[MCPTool]:
        result = await self.request("tools/list", {})
        if "error" in result:
            raise RuntimeError(f"tools/list failed: {result['error']}")
        tools = (result.get("result") or {}).get("tools") or []
        parsed: list[MCPTool] = []
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            parsed.append(
                MCPTool(
                    server=self.config.name,
                    name=str(tool["name"]),
                    description=str(tool.get("description") or ""),
                    input_schema=tool.get("inputSchema") or {"type": "object", "properties": {}},
                )
            )
        return parsed

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        result = await self.request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=120
        )
        if "error" in result:
            return ToolResult.failure(f"MCP error: {result['error']}")
        payload = result.get("result") or {}
        content = payload.get("content") or []
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(str(item.get("text", "")))
                elif item.get("type") == "resource":
                    resource = item.get("resource") or {}
                    texts.append(str(resource.get("text") or resource.get("uri") or ""))
                else:
                    texts.append(json.dumps(item, ensure_ascii=False))
        output = "\n".join(text for text in texts if text)
        if not output:
            output = json.dumps(payload, ensure_ascii=False, indent=2)
        if payload.get("isError"):
            return ToolResult.failure(output, output=output)
        return ToolResult.success(output, server=self.config.name, tool=name)

    async def stop(self) -> None:
        if self.process is None:
            return
        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), timeout=3)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self.process = None


class MCPManager:
    def __init__(self, workspace: str | Path = ".", config_path: str | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.config_path = Path(config_path).expanduser() if config_path else None
        self.clients: dict[str, MCPClient] = {}
        self.tools: list[MCPTool] = []
        self.errors: dict[str, str] = {}
        self.started = False

    def _config_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        if self.config_path:
            candidates.append(
                self.config_path
                if self.config_path.is_absolute()
                else self.workspace / self.config_path
            )
        candidates.extend(
            [
                self.workspace / ".gemma" / "mcp.json",
                self.workspace / ".opencode" / "mcp.json",
                Path.home() / ".config" / "gemma" / "mcp.json",
            ]
        )
        return candidates

    def _load_configs(self) -> list[MCPServerConfig]:
        for path in self._config_candidates():
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                self.errors[str(path)] = f"Invalid JSON: {exc}"
                continue
            servers = data.get("mcpServers") or data.get("servers") or {}
            if isinstance(servers, list):
                iterable = [(item.get("name", f"server_{i}"), item) for i, item in enumerate(servers)]
            elif isinstance(servers, dict):
                iterable = list(servers.items())
            else:
                continue
            configs: list[MCPServerConfig] = []
            for name, item in iterable:
                if not isinstance(item, dict):
                    continue
                command = item.get("command")
                if not command:
                    self.errors[str(name)] = "Missing 'command'"
                    continue
                configs.append(
                    MCPServerConfig(
                        name=str(name),
                        command=str(command),
                        args=[str(arg) for arg in item.get("args", [])],
                        env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                        cwd=item.get("cwd"),
                        disabled=bool(item.get("disabled", False)),
                    )
                )
            return configs
        return []

    async def start(self) -> None:
        # Allow an explicit retry when a previous attempt connected nothing.
        if self.started and self.clients:
            return
        self.started = True
        self.errors.clear()
        for config in self._load_configs():
            if config.disabled:
                continue
            client = MCPClient(config)
            try:
                await client.start()
                tools = await client.list_tools()
            except Exception as exc:  # noqa: BLE001
                self.errors[config.name] = str(exc)
                await client.stop()
                continue
            self.clients[config.name] = client
            self.tools.extend(tools)

    def register_tools(self, registry: ToolRegistry) -> None:
        for tool in self.tools:
            full_name = f"mcp__{_safe_name(tool.server)}__{_safe_name(tool.name)}"
            if registry.get(full_name) is not None:
                continue

            async def handler(
                context: ToolContext,
                args: dict[str, Any],
                *,
                _server: str = tool.server,
                _tool: str = tool.name,
            ) -> ToolResult:
                client = self.clients.get(_server)
                if client is None:
                    return ToolResult.failure(f"MCP server '{_server}' is not connected")
                try:
                    return await client.call_tool(_tool, args)
                except Exception as exc:  # noqa: BLE001
                    return ToolResult.failure(f"MCP tool '{_server}/{_tool}' failed: {exc}")

            registry.register(
                ToolSpec(
                    name=full_name,
                    description=f"[MCP:{tool.server}] {tool.description}".strip(),
                    parameters=tool.input_schema
                    if isinstance(tool.input_schema, dict)
                    else object_schema({}),
                    handler=handler,
                    permission="mcp",
                    max_output_chars=40_000,
                )
            )

    def status(self) -> str:
        if not self.clients and not self.errors:
            return "No MCP servers configured or connected."
        lines = ["MCP servers:"]
        for name, client in self.clients.items():
            count = sum(1 for tool in self.tools if tool.server == name)
            lines.append(f"- {name}: connected ({count} tools)")
        for name, error in self.errors.items():
            lines.append(f"- {name}: error: {error}")
        return "\n".join(lines)

    async def stop(self) -> None:
        for client in self.clients.values():
            await client.stop()
        self.clients.clear()
        self.tools.clear()
        self.started = False
