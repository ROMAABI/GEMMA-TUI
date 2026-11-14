from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import sessions.store as store_module
from config.settings import Settings
from core.permissions import PermissionRequest
from tui.app import GemmaTUI, PermissionScreen


@pytest.fixture()
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Isolate the TUI from the real config, data and network."""
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("GEMMA_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GEMMA_MODEL", "test-model")
    monkeypatch.setenv("GEMMA_WORKSPACE", str(workspace))
    monkeypatch.setattr(store_module, "data_dir", lambda: data_home / "gemma-local")

    return Settings.load()


@pytest.mark.asyncio
async def test_tui_mounts_and_runs_commands(app_env: Settings) -> None:
    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.is_running
        assert app.focused is not None and app.focused.id == "prompt"

        for command in (
            "help",
            "permissions",
            "context",
            "skills",
            "mcp",
            "lsp",
            "provider",
        ):
            await app.run_command(command, "")
            await pilot.pause()


@pytest.mark.asyncio
async def test_permission_modal_denies_on_escape(app_env: Settings) -> None:
    app = GemmaTUI(app_env)
    request = PermissionRequest(
        action="bash",
        resources=["rm -rf /tmp/example"],
        tool="bash",
        args={"command": "rm -rf /tmp/example"},
        reason="dangerous command",
    )
    result: list[str] = []

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(PermissionScreen(request), result.append)
        await pilot.pause()
        assert isinstance(app.screen, PermissionScreen)
        await pilot.press("escape")
        await pilot.pause()

    assert result == ["deny"]


@pytest.mark.asyncio
async def test_permission_modal_allow_button(app_env: Settings) -> None:
    app = GemmaTUI(app_env)
    request = PermissionRequest(
        action="write",
        resources=["notes.txt"],
        tool="write",
        args={"path": "notes.txt"},
    )
    result: list[str] = []

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(PermissionScreen(request), result.append)
        await pilot.pause()
        await pilot.click("#allow")
        await pilot.pause()

    assert result == ["allow"]


@pytest.mark.asyncio
async def test_ask_permission_roundtrip(app_env: Settings) -> None:
    app = GemmaTUI(app_env)
    request = PermissionRequest(
        action="bash",
        resources=["echo hi"],
        tool="bash",
        args={"command": "echo hi"},
    )

    async with app.run_test(size=(120, 40)) as pilot:
        task = asyncio.create_task(app._ask_permission(request))
        await pilot.pause()
        assert isinstance(app.screen, PermissionScreen)
        await pilot.click("#always")
        await pilot.pause()
        assert await asyncio.wait_for(task, timeout=5) == "always"


@pytest.mark.asyncio
async def test_init_command_creates_agents_md(app_env: Settings) -> None:
    app = GemmaTUI(app_env)
    target = Path(app_env.workspace_dir) / "AGENTS.md"

    async with app.run_test(size=(120, 40)) as pilot:
        await app.run_command("init", "")
        await pilot.pause()

    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "Project Instructions" in content
    assert "## Commands" in content


@pytest.mark.asyncio
async def test_permission_mode_command_persists(app_env: Settings) -> None:
    app = GemmaTUI(app_env)

    async with app.run_test(size=(120, 40)) as pilot:
        await app.run_command("permissions", "mode ask")
        await pilot.pause()

    assert app.settings.permission_mode == "ask"
    assert app.permission_manager.mode == "ask"
    reloaded = Settings.load()
    assert reloaded.permission_mode == "ask"


@pytest.mark.asyncio
async def test_footer_hides_items_as_width_shrinks(app_env: Settings) -> None:
    expected = {
        160: {"hints": True, "rename": True, "theme": True, "footer": True},
        100: {"hints": False, "rename": True, "theme": True, "footer": True},
        50: {"hints": False, "rename": False, "theme": True, "footer": True},
        30: {"hints": False, "rename": False, "theme": False, "footer": True},
        24: {"hints": False, "rename": False, "theme": False, "footer": False},
    }
    for width, want in expected.items():
        app = GemmaTUI(app_env)
        async with app.run_test(size=(width, 24)) as pilot:
            await pilot.pause()
            footer = app.query_one("#prompt-footer")
            hints = app.query_one("#footer-hints")
            rename = app.query_one("#cmd-rename")
            theme = app.query_one("#cmd-theme")
            assert (footer.size.width > 0) is want["footer"], width
            assert (hints.size.width > 0) is want["hints"], width
            assert (rename.size.width > 0) is want["rename"], width
            assert (theme.size.width > 0) is want["theme"], width


def test_incomplete_agent_message_reports_failed_tools() -> None:
    from tui.app import format_incomplete_agent_message

    message = format_incomplete_agent_message(
        [("bash", "Exit code: 1\n\nSTDERR:\nmkdir: Permission denied")]
    )
    assert "not confirmed complete" in message
    assert "bash" in message
    assert "Permission denied" in message
    assert "Task completed" not in message


def test_incomplete_agent_message_without_failures() -> None:
    from tui.app import format_incomplete_agent_message

    message = format_incomplete_agent_message([])
    assert "without a final answer" in message
    assert "Task completed" not in message


@pytest.mark.asyncio
async def test_warmup_is_skipped_in_headless_test_mode(app_env: Settings) -> None:
    app = GemmaTUI(app_env)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.is_headless is True
        app._start_warmup()
        assert app._warmup_task is None


@pytest.mark.asyncio
async def test_warmup_can_be_disabled_by_setting(
    app_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GemmaTUI, "is_headless", property(lambda self: False))
    app_env.agent_warmup = False
    app = GemmaTUI(app_env)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._start_warmup()
        assert app._warmup_task is None


@pytest.mark.asyncio
async def test_clicking_header_model_opens_picker(app_env: Settings) -> None:
    from tui.app import ModelPicker

    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#topbar-model")
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)
        assert app.screen.query("ModelItem")


@pytest.mark.asyncio
async def test_model_picker_selection_updates_settings(app_env: Settings) -> None:
    from tui.app import ModelItem, ModelPicker

    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        async def fake_models():
            return [(app_env.model, "current"), ("other-model.gguf", "local")]

        app._collect_models = fake_models  # type: ignore[method-assign]
        await app.open_model_picker()
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, ModelPicker)
        items = list(picker.query(ModelItem))
        picker.query_one("#model-list").index = items.index(items[-1])
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ModelPicker)
        assert app.settings.model == "other-model.gguf"


@pytest.mark.asyncio
async def test_clicking_settings_opens_menu(app_env: Settings) -> None:
    from tui.app import SettingsItem, SettingsMenu

    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-settings")
        await pilot.pause()
        assert isinstance(app.screen, SettingsMenu)
        keys = [item.key for item in app.screen.query(SettingsItem)]
        assert "model" in keys and "theme" in keys


@pytest.mark.asyncio
async def test_warmup_skipped_in_chat_mode(
    app_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GemmaTUI, "is_headless", property(lambda self: False))
    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.settings.agent_mode = False
        app._start_warmup()
        assert app._warmup_task is None
        app.settings.agent_mode = True
        app._start_warmup()
        assert app._warmup_task is not None
        app._warmup_task.cancel()


def test_smalltalk_skips_search_judge() -> None:
    from tui.app import _SMALLTALK_RE

    for text in ("hey gemma", "Hi!", "hello", "thanks", "good morning", "what's up?"):
        assert _SMALLTALK_RE.match(text.strip()), text
    for text in ("what is the weather today", "who won the game last night", "hey, search the news"):
        assert not _SMALLTALK_RE.match(text.strip()), text


@pytest.mark.asyncio
async def test_web_command_starts_shared_server(
    app_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("tui.app.webbrowser.open", lambda url: opened.append(url) or True)

    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.run_command("web", "")
        await pilot.pause()

        server = app.web_server
        assert server is not None and server.running
        assert opened and opened[-1].startswith(server.base_url)
        assert "/pair?token=" in opened[-1]

        # A second /web reuses the server and mints a fresh one-time link.
        first = server
        await app.run_command("web", "")
        await pilot.pause()
        assert app.web_server is first
        assert len(opened) == 2
        assert opened[0] != opened[1]

    assert app.web_server is None


def test_web_command_is_documented() -> None:
    from core.commands import COMMAND_DEFS, HELP_TEXT

    assert any(name == "/web" for name, _ in COMMAND_DEFS)
    assert "/web" in HELP_TEXT
    assert "Ctrl+W" in HELP_TEXT


@pytest.mark.asyncio
async def test_web_server_shares_sessions_with_the_tui(
    app_env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser talking to /web sees the TUI's chats, and vice versa."""
    import httpx

    monkeypatch.setattr("tui.app.webbrowser.open", lambda url: True)

    app = GemmaTUI(app_env)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.run_command("web", "")
        await pilot.pause()
        server = app.web_server
        assert server is not None

        with httpx.Client(base_url=server.base_url, follow_redirects=False, timeout=10) as web:
            assert web.get(server.new_pairing_url()).status_code == 302

            # A chat started in the terminal shows up in the browser.
            session_id = app.store.create_session()
            app.store.add_message(session_id, "user", "from the terminal")
            app.store.add_message(session_id, "assistant", "from gemma")
            detail = web.get(f"/api/sessions/{session_id}").json()
            assert [m["content"] for m in detail["messages"]] == [
                "from the terminal",
                "from gemma",
            ]
            listed = {row["id"] for row in web.get("/api/sessions").json()["sessions"]}
            assert session_id in listed

            # A chat created in the browser is in the TUI's session list.
            created = web.post("/api/sessions").json()["id"]
            app.store.add_message(created, "user", "from the browser")
            titles = {row.id: row.title for row in app.store.list_sessions()}
            assert created in titles

        assert server.running
    assert app.web_server is None
