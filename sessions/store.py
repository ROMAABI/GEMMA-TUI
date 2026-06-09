from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from sessions.schema import SessionSummary
from utils.paths import data_dir


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
                """
            )

    def create_session(self, title: str = "New chat") -> str:
        session_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute("insert into sessions (id, title) values (?, ?)", (session_id, title))
        return session_id

    def ensure_session(self, session_id: str | None = None) -> str:
        if session_id and self.get_session(session_id):
            return session_id
        return self.create_session()

    def get_session(self, session_id: str) -> SessionSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "select id, title, updated_at from sessions where id = ?",
                (session_id,),
            ).fetchone()
        return SessionSummary(**dict(row)) if row else None

    def list_sessions(self) -> list[SessionSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "select id, title, updated_at from sessions order by updated_at desc limit 30"
            ).fetchall()
        return [SessionSummary(**dict(row)) for row in rows]

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into messages (session_id, role, content) values (?, ?, ?)",
                (session_id, role, content),
            )
            conn.execute(
                "update sessions set title = case when title = 'New chat' and ? = 'user' then substr(?, 1, 60) else title end, updated_at = current_timestamp where id = ?",
                (role, content.replace("\n", " "), session_id),
            )

    def messages(self, session_id: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select role, content from messages where session_id = ? order by id",
                (session_id,),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def rename_session(self, session_id: str, title: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "update sessions set title = ?, updated_at = current_timestamp where id = ?",
                (title[:60], session_id),
            )

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("delete from messages where session_id = ?", (session_id,))
            conn.execute("delete from sessions where id = ?", (session_id,))

    def count_messages(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) as count from messages where session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["count"])
