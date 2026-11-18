"""Launcher tests: OpenTUI by default, Textual behind ``--classic``."""

from __future__ import annotations

import atexit

import pytest
from typer.testing import CliRunner

from app import cli
from config.settings import Settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "_ensure_server", lambda _url: True)
    monkeypatch.setattr(cli, "stop_llama_server", lambda: 0)
    monkeypatch.setattr(atexit, "register", lambda *_args, **_kwargs: None)


class _FakeTUI:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self) -> None:
        pass


def test_default_launches_opentui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: calls.append("opentui") or True)
    def fake_tui(**_kwargs):
        calls.append("classic")
        return _FakeTUI()

    monkeypatch.setattr(cli, "GemmaTUI", fake_tui)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0, result.output
    assert calls == ["opentui"]


def test_classic_skips_opentui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: calls.append("opentui") or True)
    def fake_tui(**_kwargs):
        calls.append("classic")
        return _FakeTUI()

    monkeypatch.setattr(cli, "GemmaTUI", fake_tui)

    result = runner.invoke(cli.app, ["--classic"])

    assert result.exit_code == 0, result.output
    assert calls == ["classic"]


def test_falls_back_when_opentui_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_opentui", lambda settings, session=None, **_kwargs: False)
    def fake_tui(**_kwargs):
        calls.append("classic")
        return _FakeTUI()

    monkeypatch.setattr(cli, "GemmaTUI", fake_tui)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0, result.output
    assert calls == ["classic"]
    assert "Falling back" in result.output


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
