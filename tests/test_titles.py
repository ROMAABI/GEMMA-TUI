from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from config.settings import Settings
from core import titles
from sessions.store import SessionStore


def test_clean_title_strips_noise_and_falls_back() -> None:
    assert titles.clean_title('  "**Hello World**."\n', "fallback") == "Hello World"
    assert titles.clean_title("```\nTamil Nadu Chief Minister\n```", "fallback") == "Tamil Nadu Chief Minister"
    assert titles.clean_title("   ", "which os i am using") == "which os i am using"
    assert len(titles.clean_title("x" * 100, "fallback")) == 40
    assert titles.clean_title("# Check  Linux\nDistro!", "fallback") == "Check Linux Distro"


def test_title_prompt_has_instruction_context_and_no_thinking() -> None:
    prompt = titles._title_prompt("which os i am using", "You are using Arch Linux.")
    assert titles._TITLE_INSTRUCTION in prompt
    assert "which os i am using" in prompt
    assert "You are using Arch Linux." in prompt
    assert prompt.endswith(titles._NO_THINKING)


def test_auto_never_overwrites_manual(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.sqlite3")
    sid = store.create_session()
    store.add_message(sid, "user", "which os i am using")
    store.add_message(sid, "assistant", "You are using Arch Linux.")

    assert store.needs_auto_title(sid)
    assert store.set_auto_title(sid, "Check Linux Distro")
    assert store.get_session(sid).title == "Check Linux Distro"
    assert not store.needs_auto_title(sid)

    store.rename_session(sid, "My own title")
    assert store.is_title_manual(sid)
    assert not store.set_auto_title(sid, "Overwritten")
    assert store.get_session(sid).title == "My own title"

    store.clear_session(sid)
    assert not store.is_title_manual(sid)
    assert store.get_session(sid).title == "New chat"


def test_migration_detects_raw_first_message_titles(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.sqlite3")
    raw = store.create_session()
    store.add_message(raw, "user", "who is the cm of tamil nadu")
    store.add_message(raw, "assistant", "The chief minister is ...")
    # Simulate the old server behaviour: the first message became the title.
    with store._connect() as conn:
        conn.execute(
            "update sessions set title = ? where id = ?",
            ("who is the cm of tamil nadu", raw),
        )
    generated = store.create_session()
    store.add_message(generated, "user", "hello")
    store.add_message(generated, "assistant", "Hi!")
    store.set_auto_title(generated, "Friendly Greeting")

    assert store.sessions_needing_titles() == [raw]


def test_title_manager_generates_in_background(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.sqlite3")
    sid = store.create_session()
    store.add_message(sid, "user", "which os i am using")
    store.add_message(sid, "assistant", "Arch Linux")

    async def fake_generate(*args: object, **kwargs: object) -> str:
        await asyncio.sleep(0.01)
        return "Check Linux Distro"

    monkeypatch.setattr(titles, "generate_title", fake_generate)
    manager = titles.TitleManager(store, Settings(), lambda: False)
    try:
        manager.submit(sid, "which os i am using", "Arch Linux", delay=0)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and store.get_session(sid).title == "New chat":
            time.sleep(0.05)
        assert store.get_session(sid).title == "Check Linux Distro"
    finally:
        manager.stop()
