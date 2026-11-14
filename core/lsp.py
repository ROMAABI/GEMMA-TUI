"""Lightweight LSP client and tools.

This is intentionally small: it starts a language server on demand, opens the
requested document, and exposes diagnostics, hover, definition, references, and
document symbols. It is not a full editor LSP integration.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core.tooling.types import (
    ToolContext,
    ToolResult,
    ToolSpec,
    integer_prop,
    object_schema,
    string_prop,
)

LANGUAGE_IDS = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
}

SERVER_CANDIDATES: dict[str, list[tuple[str, list[str]]]] = {
    "python": [
        ("pyright-langserver", ["--stdio"]),
        ("pylsp", []),
        ("ruff", ["server"]),
        ("jedi-language-server", []),
    ],
    "typescript": [("typescript-language-server", ["--stdio"])],
    "javascript": [("typescript-language-server", ["--stdio"])],
    "go": [("gopls", [])],
    "rust": [("rust-analyzer", [])],
    "c": [("clangd", [])],
    "cpp": [("clangd", [])],
    "java": [("jdtls", [])],
    "ruby": [("solargraph", ["stdio"])],
    "php": [("intelephense", ["--stdio"])],
}


def _path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


class LSPClient:
    def __init__(self, command: str, args: list[str], root: Path) -> None:
        self.command = command
        self.args = args
        self.root = root
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diagnostic_events: dict[str, asyncio.Event] = {}
        self.opened: set[str] = set()

    async def start(self) -> None:
        resolved = shutil.which(self.command) or self.command
        self.process = await asyncio.create_subprocess_exec(
            resolved,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        asyncio.create_task(self._drain_stderr())
        result = await self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": _path_to_uri(self.root),
                "capabilities": {
                    "textDocument": {
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "definition": {},
                        "references": {},
                        "documentSymbol": {},
                        "publishDiagnostics": {},
                    }
                },
                "workspaceFolders": [
                    {"uri": _path_to_uri(self.root), "name": self.root.name}
                ],
            },
            timeout=30,
        )
        if "error" in result:
            raise RuntimeError(f"LSP initialize failed: {result['error']}")
        await self.notify("initialized", {})

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break

    async def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        reader = self.process.stdout
        while True:
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if not line:
                    return
                decoded = line.decode("ascii", errors="replace").strip()
                if not decoded:
                    break
                if ":" in decoded:
                    key, _, value = decoded.partition(":")
                    headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0") or 0)
            if length <= 0:
                continue
            body = await reader.readexactly(length)
            try:
                message = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if "id" in message and ("result" in message or "error" in message):
                future = self._pending.pop(int(message["id"]), None)
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            method = message.get("method")
            if method == "textDocument/publishDiagnostics":
                params = message.get("params") or {}
                uri = params.get("uri", "")
                self.diagnostics[uri] = params.get("diagnostics") or []
                event = self._diagnostic_events.get(uri)
                if event is not None:
                    event.set()

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP server is not running")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        async with self._write_lock:
            self.process.stdin.write(header + body)
            await self.process.stdin.drain()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
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

    async def open_document(self, path: Path, language_id: str) -> str:
        uri = _path_to_uri(path)
        if uri in self.opened:
            return uri
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Could not read {path}: {exc}") from exc
        self._diagnostic_events.setdefault(uri, asyncio.Event())
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        self.opened.add(uri)
        return uri

    async def wait_diagnostics(self, uri: str, timeout: float = 4.0) -> list[dict[str, Any]]:
        event = self._diagnostic_events.setdefault(uri, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self.diagnostics.get(uri, [])

    async def stop(self) -> None:
        if self.process is None:
            return
        try:
            await self.request("shutdown", {}, timeout=3)
            await self.notify("exit", {})
        except Exception:
            pass
        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), timeout=3)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
        self.process = None


@dataclass
class LSPStatus:
    language: str
    command: str
    running: bool


class LSPManager:
    def __init__(self, workspace: str | Path = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.clients: dict[str, LSPClient] = {}
        self.errors: dict[str, str] = {}

    def _language_for(self, path: Path) -> str | None:
        return LANGUAGE_IDS.get(path.suffix.lower())

    def _server_for(self, language: str) -> tuple[str, list[str]] | None:
        for command, args in SERVER_CANDIDATES.get(language, []):
            if shutil.which(command):
                return command, args
        return None

    async def _client_for(self, path: Path) -> tuple[LSPClient, str]:
        language = self._language_for(path)
        if language is None:
            raise RuntimeError(f"No LSP language mapping for {path.suffix or path.name}")
        if language in self.clients:
            return self.clients[language], language
        server = self._server_for(language)
        if server is None:
            candidates = ", ".join(
                command for command, _ in SERVER_CANDIDATES.get(language, [])
            )
            raise RuntimeError(
                f"No language server installed for {language}. Tried: {candidates}"
            )
        client = LSPClient(server[0], server[1], self.workspace)
        try:
            await client.start()
        except Exception as exc:  # noqa: BLE001
            self.errors[language] = str(exc)
            raise RuntimeError(f"Could not start {server[0]}: {exc}") from exc
        self.clients[language] = client
        return client, language

    async def diagnostics(self, path: Path) -> ToolResult:
        client, language = await self._client_for(path)
        uri = await client.open_document(path, language)
        items = await client.wait_diagnostics(uri)
        if not items:
            return ToolResult.success(f"No diagnostics for {path.name}.", count=0)
        severity = {1: "error", 2: "warning", 3: "info", 4: "hint"}
        lines: list[str] = []
        for item in items:
            start = item.get("range", {}).get("start", {})
            line = int(start.get("line", 0)) + 1
            char = int(start.get("character", 0)) + 1
            level = severity.get(item.get("severity"), "info")
            message = str(item.get("message", "")).strip().replace("\n", " ")
            lines.append(f"{path.name}:{line}:{char}: {level}: {message}")
        return ToolResult.success("\n".join(lines), count=len(lines))

    async def _position_request(
        self, path: Path, line: int, character: int, method: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        client, language = await self._client_for(path)
        uri = await client.open_document(path, language)
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": max(0, line - 1), "character": max(0, character - 1)},
        }
        if extra:
            params.update(extra)
        return await client.request(method, params)

    async def hover(self, path: Path, line: int, character: int) -> ToolResult:
        response = await self._position_request(
            path, line, character, "textDocument/hover"
        )
        if "error" in response:
            return ToolResult.failure(str(response["error"]))
        contents = (response.get("result") or {}).get("contents")
        text = _render_hover(contents)
        return ToolResult.success(text or "No hover information.", text=text)

    async def definition(self, path: Path, line: int, character: int) -> ToolResult:
        response = await self._position_request(
            path, line, character, "textDocument/definition"
        )
        if "error" in response:
            return ToolResult.failure(str(response["error"]))
        return _render_locations(response.get("result"), "No definition found.")

    async def references(self, path: Path, line: int, character: int) -> ToolResult:
        response = await self._position_request(
            path,
            line,
            character,
            "textDocument/references",
            {"context": {"includeDeclaration": True}},
        )
        if "error" in response:
            return ToolResult.failure(str(response["error"]))
        return _render_locations(response.get("result"), "No references found.")

    async def symbols(self, path: Path) -> ToolResult:
        client, language = await self._client_for(path)
        uri = await client.open_document(path, language)
        response = await client.request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )
        if "error" in response:
            return ToolResult.failure(str(response["error"]))
        symbols = response.get("result") or []
        lines: list[str] = []
        for symbol in symbols:
            if not isinstance(symbol, dict):
                continue
            kind = _symbol_kind(symbol.get("kind"))
            start = (symbol.get("range") or symbol.get("location", {}).get("range") or {}).get(
                "start", {}
            )
            line = int(start.get("line", 0)) + 1
            lines.append(f"{line}: {symbol.get('name', '?')} ({kind})")
        if not lines:
            return ToolResult.success("No symbols found.")
        return ToolResult.success("\n".join(lines), count=len(lines))

    def status(self) -> str:
        if not self.clients and not self.errors:
            return "No LSP servers started."
        lines = ["LSP servers:"]
        for language, client in self.clients.items():
            lines.append(
                f"- {language}: {client.command} (running, {len(client.opened)} open docs)"
            )
        for language, error in self.errors.items():
            lines.append(f"- {language}: error: {error}")
        return "\n".join(lines)

    async def stop(self) -> None:
        for client in self.clients.values():
            await client.stop()
        self.clients.clear()


def _render_hover(contents: Any) -> str:
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value", "")))
        return "\n\n".join(part for part in parts if part)
    return str(contents)


def _render_locations(result: Any, empty_message: str) -> ToolResult:
    if not result:
        return ToolResult.success(empty_message, count=0)
    items = result if isinstance(result, list) else [result]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        if not uri:
            continue
        path = _uri_to_path(uri)
        start = (item.get("range") or item.get("targetSelectionRange") or {}).get(
            "start", {}
        )
        line = int(start.get("line", 0)) + 1
        char = int(start.get("character", 0)) + 1
        lines.append(f"{path}:{line}:{char}")
    if not lines:
        return ToolResult.success(empty_message, count=0)
    return ToolResult.success("\n".join(lines), count=len(lines))


def _symbol_kind(kind: Any) -> str:
    names = {
        1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
        6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
        11: "interface", 12: "function", 13: "variable", 14: "constant",
        15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
        20: "key", 21: "null", 22: "enum member", 23: "struct", 24: "event",
        25: "operator", 26: "type parameter",
    }
    try:
        return names.get(int(kind), "symbol")
    except (TypeError, ValueError):
        return "symbol"


def lsp_tools(manager: LSPManager) -> list[ToolSpec]:
    def _path(context: ToolContext, args: dict[str, Any]) -> Path:
        raw = args.get("path") or ""
        if not raw:
            raise ValueError("A 'path' argument is required")
        return context.resolve(raw, must_exist=True)

    async def diagnostics(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            return await manager.diagnostics(_path(context, args))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(str(exc))

    async def hover(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            return await manager.hover(
                _path(context, args), int(args.get("line", 1)), int(args.get("character", 1))
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(str(exc))

    async def definition(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            return await manager.definition(
                _path(context, args), int(args.get("line", 1)), int(args.get("character", 1))
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(str(exc))

    async def references(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            return await manager.references(
                _path(context, args), int(args.get("line", 1)), int(args.get("character", 1))
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(str(exc))

    async def symbols(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            return await manager.symbols(_path(context, args))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(str(exc))

    return [
        ToolSpec(
            name="lsp_diagnostics",
            description="Get compiler/linter diagnostics for a file from its language server.",
            parameters=object_schema({"path": string_prop("Workspace-relative file path")}, ["path"]),
            handler=diagnostics,
            permission="read",
        ),
        ToolSpec(
            name="lsp_hover",
            description="Get type/documentation information at a 1-based line and character.",
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative file path"),
                    "line": integer_prop("1-based line", minimum=1),
                    "character": integer_prop("1-based character", minimum=1),
                },
                ["path", "line", "character"],
            ),
            handler=hover,
            permission="read",
        ),
        ToolSpec(
            name="lsp_definition",
            description="Find the definition of the symbol at a 1-based line and character.",
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative file path"),
                    "line": integer_prop("1-based line", minimum=1),
                    "character": integer_prop("1-based character", minimum=1),
                },
                ["path", "line", "character"],
            ),
            handler=definition,
            permission="read",
        ),
        ToolSpec(
            name="lsp_references",
            description="Find references to the symbol at a 1-based line and character.",
            parameters=object_schema(
                {
                    "path": string_prop("Workspace-relative file path"),
                    "line": integer_prop("1-based line", minimum=1),
                    "character": integer_prop("1-based character", minimum=1),
                },
                ["path", "line", "character"],
            ),
            handler=references,
            permission="read",
        ),
        ToolSpec(
            name="lsp_symbols",
            description="List document symbols (functions, classes, methods) in a file.",
            parameters=object_schema({"path": string_prop("Workspace-relative file path")}, ["path"]),
            handler=symbols,
            permission="read",
        ),
    ]
