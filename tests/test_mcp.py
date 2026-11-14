from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.mcp import MCPManager
from core.tooling.registry import ToolRegistry
from core.tooling.types import ToolContext

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")


def write_config(workspace: Path) -> Path:
    config = workspace / ".gemma" / "mcp.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(FAKE_SERVER)],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return config


@pytest.mark.asyncio
async def test_mcp_start_list_and_call(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    manager = MCPManager(tmp_path, str(config))
    try:
        await manager.start()
        assert manager.errors == {}
        assert {tool.name for tool in manager.tools} == {"echo", "fail"}

        registry = ToolRegistry()
        manager.register_tools(registry)
        names = registry.names()
        assert "mcp__fake__echo" in names
        assert "mcp__fake__fail" in names

        context = ToolContext(root=tmp_path)
        result = await registry.execute(
            "mcp__fake__echo", {"text": "hello"}, context
        )
        assert result.ok
        assert result.output == "echo: hello"

        failed = await registry.execute("mcp__fake__fail", {}, context)
        assert not failed.ok
        assert "boom" in failed.output
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_mcp_unknown_tool_returns_error(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    manager = MCPManager(tmp_path, str(config))
    try:
        await manager.start()
        client = manager.clients["fake"]
        result = await client.call_tool("missing", {})
        assert not result.ok
        assert "unknown tool" in result.output
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_mcp_status_and_missing_config(tmp_path: Path) -> None:
    manager = MCPManager(tmp_path, str(tmp_path / "missing.json"))
    await manager.start()
    assert manager.status() == "No MCP servers configured or connected."
    await manager.stop()


@pytest.mark.asyncio
async def test_mcp_invalid_json_records_error(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text("{not json", encoding="utf-8")
    manager = MCPManager(tmp_path, str(config))
    await manager.start()
    assert str(config) in manager.errors
    assert "Invalid JSON" in manager.errors[str(config)]
    await manager.stop()
