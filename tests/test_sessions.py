from __future__ import annotations

import sqlite3
from pathlib import Path

from sessions.store import SessionStore


def test_session_roundtrip_with_metadata(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session_id = store.create_session("Test")
    store.add_message(session_id, "user", "hello")
    message_id = store.add_message(
        session_id,
        "assistant",
        "",
        metadata={"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
    )
    assert message_id > 0
    store.add_message(
        session_id,
        "tool",
        "file contents",
        tool_call_id="c1",
        name="read",
        metadata={"ok": True},
    )

    messages = store.messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["metadata"]["tool_calls"][0]["id"] == "c1"
    assert messages[2]["tool_call_id"] == "c1"
    assert messages[2]["name"] == "read"

    context = store.context_messages(session_id)
    assert context[1]["tool_calls"][0]["id"] == "c1"


def test_checkpoint_context_starts_at_latest_checkpoint(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session_id = store.create_session("Test")
    store.add_message(session_id, "user", "old request")
    store.add_message(session_id, "assistant", "old answer")
    store.add_message(
        session_id,
        "system",
        "<conversation-checkpoint>\nsummary\n</conversation-checkpoint>",
        metadata={"kind": "checkpoint"},
    )
    store.add_message(session_id, "user", "new request")

    context = store.context_messages(session_id)
    assert len(context) == 2
    assert context[0]["role"] == "system"
    assert context[1]["content"] == "new request"

    # Display still sees the full history.
    assert len(store.messages(session_id)) == 4


def test_events_and_delete(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session_id = store.create_session("Test")
    store.add_message(session_id, "user", "hi")
    store.add_event(session_id, "tool_start", {"name": "read"})
    events = store.events(session_id)
    assert events[0]["type"] == "tool_start"
    assert events[0]["data"]["name"] == "read"

    store.delete_session(session_id)
    assert store.messages(session_id) == []
    assert store.events(session_id) == []


def test_migrates_old_database(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table sessions (
            id text primary key,
            title text not null,
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create table messages (
            id integer primary key autoincrement,
            session_id text not null,
            role text not null,
            content text not null,
            created_at text not null default current_timestamp
        );
        insert into sessions (id, title) values ('old', 'Old');
        insert into messages (session_id, role, content) values ('old', 'user', 'hello');
        """
    )
    conn.commit()
    conn.close()

    store = SessionStore(path)
    store.add_message("old", "assistant", "world", metadata={"ok": True})
    messages = store.messages("old")
    assert len(messages) == 2
    assert messages[1]["metadata"] == {"ok": True}

    with sqlite3.connect(path) as check:
        columns = {row[1] for row in check.execute("pragma table_info(messages)")}
    assert {"metadata", "tool_call_id", "name"} <= columns
