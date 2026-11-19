from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sessions.schema import SessionSummary
from utils.paths import data_dir


def format_session_timestamp(updated_at_str: str | None) -> str:
    """Format an SQLite timestamp into a relative human-readable label."""
    if not updated_at_str:
        return ""
    try:
        clean_str = updated_at_str.replace("T", " ").split(".")[0]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        today = now.date()
        dt_date = dt.date()

        if dt_date == today:
            return dt.strftime("%I:%M %p")
        elif dt_date == today - timedelta(days=1):
            return "Yesterday"
        elif dt.year == now.year:
            return dt.strftime("%b %d")
        else:
            return dt.strftime("%b %d, %Y")
    except Exception:
        return updated_at_str[:10] if updated_at_str else ""


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "sessions.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists sessions (
                    id text primary key,
                    title text not null,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );

                create table if not exists messages (
                    id integer primary key autoincrement,
                    session_id text not null,
                    role text not null,
                    content text not null,
                    created_at text not null default current_timestamp,
                    foreign key(session_id) references sessions(id)
                );

                create table if not exists events (
                    id integer primary key autoincrement,
                    session_id text not null,
                    type text not null,
                    data text not null default '{}',
                    created_at text not null default current_timestamp
                );

                create index if not exists idx_messages_session on messages(session_id, id);
                create index if not exists idx_events_session on events(session_id, id);
                """
            )
            self._migrate(conn)
        self.cleanup_empty_sessions()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive migrations for databases created by older versions."""
        migrations = {
            "sessions": {
                "directory": "text",
                "model": "text",
                "summary": "text",
                "tokens": "integer not null default 0",
                "metadata": "text not null default '{}'",
                "title_manual": "integer not null default 0",
            },
            "messages": {
                "metadata": "text not null default '{}'",
                "tool_call_id": "text",
                "name": "text",
            },
        }
        for table, columns in migrations.items():
            existing = {
                row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()
            }
            for column, declaration in columns.items():
                if column not in existing:
                    conn.execute(f"alter table {table} add column {column} {declaration}")

    def cleanup_empty_sessions(self) -> int:
        """Remove any sessions that have no messages."""
        with self._connect() as conn:
            cur = conn.execute(
                "delete from sessions where not exists "
                "(select 1 from messages where messages.session_id = sessions.id)"
            )
            return cur.rowcount

    def create_session(
        self,
        title: str = "New chat",
        *,
        directory: str | None = None,
        model: str | None = None,
    ) -> str:
        session_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "insert into sessions (id, title, directory, model) values (?, ?, ?, ?)",
                (session_id, title, directory, model),
            )
        return session_id

    def ensure_session(self, session_id: str | None = None) -> str:
        if session_id and self.get_session(session_id):
            return session_id
        return session_id or uuid.uuid4().hex[:12]

    def get_session(self, session_id: str) -> SessionSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "select id, title, updated_at from sessions where id = ?",
                (session_id,),
            ).fetchone()
        return SessionSummary(**dict(row)) if row else None

    def list_sessions(self, include_empty: bool = False) -> list[SessionSummary]:
        query = (
            "select id, title, updated_at from sessions order by updated_at desc limit 30"
            if include_empty
            else "select s.id, s.title, s.updated_at from sessions s "
            "where exists (select 1 from messages m where m.session_id = s.id) "
            "order by s.updated_at desc limit 30"
        )
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [SessionSummary(**dict(row)) for row in rows]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> int:
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "insert or ignore into sessions (id, title) values (?, ?)",
                (session_id, "New chat"),
            )
            cursor = conn.execute(
                "insert into messages "
                "(session_id, role, content, metadata, tool_call_id, name) "
                "values (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, payload, tool_call_id, name),
            )
            conn.execute(
                "update sessions set updated_at = current_timestamp where id = ?",
                (session_id,),
            )
            return int(cursor.lastrowid or 0)

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return every stored message (for display and replay)."""
        with self._connect() as conn:
            rows = conn.execute(
                "select role, content, created_at, metadata, tool_call_id, name "
                "from messages where session_id = ? order by id",
                (session_id,),
            ).fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "metadata": _loads(row["metadata"]),
                "tool_call_id": row["tool_call_id"],
                "name": row["name"],
            }
            for row in rows
        ]

    def context_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return messages for model context, starting at the latest checkpoint.

        Tool calls stored in message metadata are rehydrated into the OpenAI
        assistant message shape so a resumed session keeps its tool history.
        """
        all_messages = self.messages(session_id)
        start = 0
        for index, message in enumerate(all_messages):
            metadata = message.get("metadata") or {}
            if message.get("role") == "system" and metadata.get("kind") == "checkpoint":
                start = index
        selected = all_messages[start:]
        result: list[dict[str, Any]] = []
        for message in selected:
            item: dict[str, Any] = {
                "role": message["role"],
                "content": message["content"],
            }
            metadata = message.get("metadata") or {}
            if metadata.get("tool_calls"):
                item["tool_calls"] = metadata["tool_calls"]
            if metadata.get("attachments"):
                item["attachments"] = metadata["attachments"]
            if message.get("tool_call_id"):
                item["tool_call_id"] = message["tool_call_id"]
            if message.get("name"):
                item["name"] = message["name"]
            result.append(item)
        return result

    def add_event(self, session_id: str, event_type: str, data: dict[str, Any] | None = None) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "insert into events (session_id, type, data) values (?, ?, ?)",
                (session_id, event_type, json.dumps(data or {}, ensure_ascii=False)),
            )
            return int(cursor.lastrowid or 0)

    def events(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select type, data, created_at from events "
                "where session_id = ? order by id limit ?",
                (session_id, limit),
            ).fetchall()
        return [
            {
                "type": row["type"],
                "data": _loads(row["data"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def set_summary(self, session_id: str, summary: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "update sessions set summary = ?, updated_at = current_timestamp where id = ?",
                (summary, session_id),
            )

    def get_summary(self, session_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "select summary from sessions where id = ?", (session_id,)
            ).fetchone()
        return str(row["summary"] or "") if row else ""

    def rename_session(self, session_id: str, title: str) -> None:
        """Rename a session explicitly; auto-titles will never overwrite it."""
        with self._connect() as conn:
            conn.execute(
                "update sessions set title = ?, title_manual = 1, "
                "updated_at = current_timestamp where id = ?",
                (title[:60], session_id),
            )

    def set_auto_title(self, session_id: str, title: str) -> bool:
        """Store a generated title, unless the user renamed this session."""
        with self._connect() as conn:
            cur = conn.execute(
                "update sessions set title = ?, updated_at = current_timestamp "
                "where id = ? and title_manual = 0",
                (title[:60], session_id),
            )
            return cur.rowcount > 0

    def is_title_manual(self, session_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "select title_manual from sessions where id = ?", (session_id,)
            ).fetchone()
        return bool(row and row["title_manual"])

    def first_exchange(self, session_id: str) -> tuple[str, str]:
        """Return the first user message and first assistant reply."""
        with self._connect() as conn:
            user = conn.execute(
                "select content from messages where session_id = ? and role = 'user' "
                "order by id limit 1",
                (session_id,),
            ).fetchone()
            reply = conn.execute(
                "select content from messages where session_id = ? and role = 'assistant' "
                "order by id limit 1",
                (session_id,),
            ).fetchone()
        return (
            str(user["content"] or "") if user else "",
            str(reply["content"] or "") if reply else "",
        )

    def needs_auto_title(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if session is None or self.is_title_manual(session_id):
            return False
        first, _ = self.first_exchange(session_id)
        clean = " ".join(first.split())
        if not clean:
            return False
        if session.title in ("", "New chat"):
            return True
        return session.title in (clean[:60], clean[:47] + "…", clean[:47] + "...")

    def sessions_needing_titles(self) -> list[str]:
        """Sessions whose title is still the raw first-message placeholder."""
        with self._connect() as conn:
            ids = [
                str(row["id"])
                for row in conn.execute(
                    "select id from sessions where title_manual = 0 order by updated_at desc"
                ).fetchall()
            ]
        return [session_id for session_id in ids if self.needs_auto_title(session_id)]

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("delete from messages where session_id = ?", (session_id,))
            conn.execute("delete from events where session_id = ?", (session_id,))
            conn.execute("delete from sessions where id = ?", (session_id,))

    def clear_session(self, session_id: str) -> None:
        """Remove a session's messages but keep the session itself."""
        with self._connect() as conn:
            conn.execute("delete from messages where session_id = ?", (session_id,))
            conn.execute("delete from events where session_id = ?", (session_id,))
            conn.execute(
                "update sessions set title = 'New chat', title_manual = 0, "
                "updated_at = current_timestamp where id = ?",
                (session_id,),
            )

    def count_messages(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) as count from messages where session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["count"])
