"""Launcher tests: the OpenTUI client is the only terminal UI."""

from __future__ import annotations

import atexit
from subprocess import CompletedProcess

import pytest
from typer.testing import CliRunner

from app import cli
from config.settings import Settings

runner = CliRunner()
_REAL_ENSURE_DEPS = cli._ensure_tui_deps


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "_ensure_server", lambda _url: True)
    monkeypatch.setattr(cli, "stop_llama_server", lambda: 0)
    monkeypatch.setattr(cli, "_ensure_tui_deps", lambda _bun: True)
    monkeypatch.setattr(atexit, "register", lambda *_args, **_kwargs: None)


def test_default_launches_opentui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "_run_opentui", lambda settings, session=None, **_kwargs: calls.append("opentui") or True
    )

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0, result.output
    assert calls == ["opentui"]


def test_classic_option_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: True)

    result = runner.invoke(cli.app, ["--classic"])

    assert result.exit_code != 0
    assert "--classic" in result.output


def test_opentui_failure_exits_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: False)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 1
    assert "Falling back" not in result.output


def test_first_launch_runs_the_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object):
        seen["command"] = command
        seen["env"] = kwargs.get("env", {})
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "_opentui_ready", lambda _bun: True)
    monkeypatch.setattr(cli, "_ensure_tui_deps", _REAL_ENSURE_DEPS)

    assert cli._ensure_tui_deps("/usr/bin/bun") is True
    assert seen["command"] == ["bash", str(cli.INSTALL_SCRIPT)]
    assert (seen["env"] or {}).get("BUN") == "/usr/bin/bun"  # type: ignore[union-attr]


def test_failed_install_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object):
        return CompletedProcess(command, 1, "", "bun: not found")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "_ensure_tui_deps", _REAL_ENSURE_DEPS)

    assert cli._ensure_tui_deps("/usr/bin/bun") is False


def test_opentui_ready_needs_bun_and_install(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "TUI_DIR", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.tsx").write_text("")
    (tmp_path / "node_modules").mkdir()

    assert cli._opentui_ready("/usr/bin/bun") is True
    assert cli._opentui_ready(None) is False

    (tmp_path / "node_modules").rmdir()
    assert cli._opentui_ready("/usr/bin/bun") is False


def test_session_option_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(
        cli, "_run_opentui", lambda settings, session=None, **_kwargs: seen.append(session) or True
    )

    result = runner.invoke(cli.app, ["--session", "abc123"])

    assert result.exit_code == 0, result.output
    assert seen == ["abc123"]
    assert isinstance(Settings.load(), Settings)


def test_does_not_stop_a_pre_existing_llama_server(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[object] = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))
    monkeypatch.setattr(cli, "server_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: True)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0, result.output
    assert registered == []


def test_stops_the_llama_server_it_started(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[object] = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))
    monkeypatch.setattr(cli, "server_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: True)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0, result.output
    assert registered == [cli.stop_llama_server]


def test_continue_flag_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def fake_run(settings, session=None, *, continue_last=False):
        seen.append(continue_last)
        return True

    monkeypatch.setattr(cli, "_run_opentui", fake_run)
    result = runner.invoke(cli.app, ["--continue"])
    assert result.exit_code == 0, result.output
    assert seen == [True]

    result = runner.invoke(cli.app, ["-c"])
    assert result.exit_code == 0, result.output
    assert seen == [True, True]
