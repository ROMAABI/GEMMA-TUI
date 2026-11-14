from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.lsp import LSPClient, LSPManager

FAKE_SERVER = Path(__file__).with_name("fake_lsp_server.py")


@pytest.fixture()
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.py"
    path.write_text("def main():\n    return 1\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_lsp_client_requests(tmp_path: Path, source_file: Path) -> None:
    client = LSPClient(sys.executable, [str(FAKE_SERVER)], tmp_path)
    try:
        await client.start()
        uri = await client.open_document(source_file, "python")

        diagnostics = await client.wait_diagnostics(uri, timeout=5)
        assert len(diagnostics) == 1
        assert diagnostics[0]["message"] == "fake error"

        hover = await client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}},
        )
        assert hover["result"]["contents"]["value"] == "**hover text**"

        definition = await client.request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}},
        )
        assert definition["result"][0]["range"]["start"]["line"] == 4
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_lsp_manager_tools(tmp_path: Path, source_file: Path) -> None:
    manager = LSPManager(tmp_path)
    manager._server_for = lambda language: (sys.executable, [str(FAKE_SERVER)])  # type: ignore[method-assign]
    try:
        diagnostics = await manager.diagnostics(source_file)
        assert diagnostics.ok
        assert "sample.py:1:1: error: fake error" in diagnostics.output

        hover = await manager.hover(source_file, 1, 1)
        assert hover.ok
        assert "hover text" in hover.output

        definition = await manager.definition(source_file, 1, 1)
        assert definition.ok
        assert "sample.py:5:3" in definition.output

        references = await manager.references(source_file, 1, 1)
        assert references.ok
        assert "sample.py:2:1" in references.output

        symbols = await manager.symbols(source_file)
        assert symbols.ok
        assert "main (function)" in symbols.output
        assert "3: main" in symbols.output

        status = manager.status()
        assert "python" in status
        assert "running" in status
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_lsp_missing_server_reports_helpful_error(tmp_path: Path, monkeypatch) -> None:
    manager = LSPManager(tmp_path)
    monkeypatch.setattr("core.lsp.shutil.which", lambda command: None)
    path = tmp_path / "sample.py"
    path.write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="No language server installed for python"):
        await manager.diagnostics(path)
