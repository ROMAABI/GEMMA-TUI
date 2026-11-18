from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

import core.webui as webui
from config.settings import Settings
from sessions.store import SessionStore


class FakeClient:
    """Streams a fixed reply without touching llama.cpp."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def stream_chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        for token in ("Hel", "lo ", "there"):
            yield token


@pytest.fixture()
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(webui, "LlamaClient", FakeClient)
    # Keep the developer's real config file untouched by the tests.
    monkeypatch.setattr(Settings, "save_config", lambda self: None)
    store = SessionStore(tmp_path / "sessions.sqlite3")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>gemma</title>", encoding="utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('gemma')", encoding="utf-8")

    server = webui.GemmaWebServer(Settings(), port=0, store=store, dist=dist)
    base = server.start()
    try:
        yield server, base, store
    finally:
        server.stop()


@contextmanager
def _paired_client(base: str, server: webui.GemmaWebServer):
    with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as client:
        response = client.get(server.new_pairing_url())
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        yield client


def test_pairing_is_required_and_single_use(web) -> None:
    server, base, _store = web
    with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as client:
        page = client.get("/")
        assert page.status_code == 403
        assert "Pair this browser" in page.text

        api = client.get("/api/info")
        assert api.status_code == 401

        pair_url = server.new_pairing_url()
        first = client.get(pair_url)
        assert first.status_code == 302
        assert "gemma_web=" in first.headers["set-cookie"]
        assert "HttpOnly" in first.headers["set-cookie"]

        # The cookie now opens the UI and the API.
        assert client.get("/").status_code == 200
        info = client.get("/api/info").json()
        assert info["app"] == "gemma"
        assert info["model"] == Settings().model

        # Pairing links work once.
        assert client.get(pair_url).status_code == 403


def test_session_crud_api(web) -> None:
    server, base, store = web
    with _paired_client(base, server) as client:
        created = client.post("/api/sessions")
        assert created.status_code == 201
        session_id = created.json()["id"]

        store.add_message(session_id, "user", "hello from the tui")
        sessions = client.get("/api/sessions").json()["sessions"]
        row = next(item for item in sessions if item["id"] == session_id)
        # Titles are generated in the background; a plain first message no
        # longer becomes the session title.
        assert row["title"] == "New chat"
        assert row["message_count"] == 1
        assert row["updated_at"].endswith("Z")

        detail = client.get(f"/api/sessions/{session_id}").json()
        assert detail["messages"][0]["role"] == "user"
        assert detail["messages"][0]["content"] == "hello from the tui"

        renamed = client.patch(f"/api/sessions/{session_id}", json={"title": "Renamed"})
        assert renamed.status_code == 200
        assert client.get(f"/api/sessions/{session_id}").json()["title"] == "Renamed"

        cleared = client.post(f"/api/sessions/{session_id}/clear")
        assert cleared.status_code == 200
        assert client.get(f"/api/sessions/{session_id}").json()["messages"] == []

        deleted = client.delete(f"/api/sessions/{session_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_chat_streams_and_persists(web) -> None:
    server, base, store = web
    with _paired_client(base, server) as client:
        session_id = client.post("/api/sessions").json()["id"]
        events: list[dict] = []
        with client.stream(
            "POST",
            "/api/chat",
            json={"session_id": session_id, "message": "say hello"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                events.append(json.loads(payload))

        assert events[0]["type"] == "start"
        assert events[0]["session_id"] == session_id
        assert "".join(e["content"] for e in events if e["type"] == "token") == "Hello there"
        done = next(e for e in events if e["type"] == "done")
        assert done["tokens"] == 3
        assert done["elapsed"] >= 0

        messages = store.messages(session_id)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "Hello there"


def test_static_assets_and_spa_fallback(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "gemma" in index.text

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert "immutable" in asset.headers["cache-control"]

        spa = client.get("/chats/abc123")
        assert spa.status_code == 200
        assert "gemma" in spa.text

        missing = client.get("/assets/missing.js")
        assert missing.status_code == 404


def test_missing_build_explains_how_to_build(tmp_path: Path) -> None:
    server = webui.GemmaWebServer(
        Settings(),
        port=0,
        store=SessionStore(tmp_path / "sessions.sqlite3"),
        dist=tmp_path / "does-not-exist",
    )
    base = server.start()
    try:
        with _paired_client(base, server) as client:
            page = client.get("/")
            assert page.status_code == 503
            assert "npm run build" in page.text
    finally:
        server.stop()


def test_models_and_commands_api(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        payload = client.get("/api/models").json()
        assert payload["active"] == server.settings.model
        names = [item["name"] for item in payload["models"]]
        assert server.settings.model in names
        assert payload["models"][0]["active"] is True

        switched = client.post("/api/models", json={"name": "test-model"})
        assert switched.status_code == 200
        assert switched.json()["active"] == "test-model"
        assert server.settings.model == "test-model"
        assert client.get("/api/info").json()["model"] == "test-model"

        assert client.post("/api/models", json={"name": ""}).status_code == 400

        commands = client.get("/api/commands").json()["commands"]
        assert any(item["name"] == "/new" for item in commands)
        assert all(item["name"].startswith("/") for item in commands)


def test_pair_endpoint_accepts_get_and_post(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        via_get = client.get("/api/pair").json()["pair"]
        via_post = client.post("/api/pair").json()["pair"]
        assert via_get.startswith(base)
        assert via_post.startswith(base)
        assert via_get != via_post

        with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as anon:
            assert anon.get(via_get).status_code == 403  # superseded by the newer link
            assert anon.get(via_post).status_code == 302


def test_pair_endpoint_mints_a_fresh_link(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        first = client.get("/api/pair").json()["pair"]
        second = client.get("/api/pair").json()["pair"]
        assert first.startswith(base)
        assert "/pair?token=" in first
        assert first != second

        with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as anon:
            assert anon.get(first).status_code == 403  # superseded by the newer link
            assert anon.get(second).status_code == 302


class FakeOrchestrator:
    """Asks for permission once, then reports the answer as text."""

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs

    async def run(self, messages):  # type: ignore[no-untyped-def]
        from core.agent import AgentEvent
        from core.permissions import PermissionRequest

        yield AgentEvent("step_start", {"step": 1, "max_steps": 1})
        approval = self.kwargs["approval"]
        decision = await approval(
            PermissionRequest(
                action="write",
                resources=["notes.txt"],
                tool="write_file",
                args={"path": "notes.txt"},
                reason="test approval",
            )
        )
        yield AgentEvent("text", f"decision={decision}")
        yield AgentEvent("complete", f"decision={decision}")


def test_provider_catalog_lists_local_first(web) -> None:
    server, base, _store = web
    from core.provider_catalog import catalog

    payload = catalog(server.settings)
    assert payload["default"] == "llama.cpp"
    assert payload["providers"][0]["id"] == "llama.cpp"
    assert payload["providers"][0]["is_default"] is True
    assert payload["active"]["provider"] == "llama.cpp"
    assert payload["providers"][0]["models"][0]["name"] == server.settings.model

    with _paired_client(base, server) as client:
        api = client.get("/api/providers").json()
        assert api["providers"][0]["id"] == "llama.cpp"
        assert client.get("/api/info").json()["provider"] == "llama.cpp"


def test_switch_provider_and_model(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        names = [
            model["name"]
            for model in client.get("/api/providers").json()["providers"][0]["models"]
        ]
        targets = [name for name in names if name != server.settings.model]
        if not targets:
            pytest.skip("only one local model is available")
        target = targets[0]
        switched = client.post(
            "/api/models",
            json={"provider": "llama.cpp", "name": target},
        )
        assert switched.status_code == 200
        assert server.settings.model == target
        assert switched.json()["provider"]["model"] == target

        assert (
            client.post(
                "/api/models", json={"provider": "llama.cpp", "name": "missing"}
            ).status_code
            == 400
        )

        unknown = client.post(
            "/api/models",
            json={"provider": "nope", "name": "x"},
        )
        assert unknown.status_code == 400


def test_mode_switch_api(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        chat = client.post("/api/mode", json={"mode": "chat"})
        assert chat.status_code == 200
        assert server.settings.agent_mode is False
        assert client.get("/api/info").json()["mode"] == "chat"

        agent = client.post("/api/mode", json={"mode": "agent"})
        assert agent.status_code == 200
        assert server.settings.agent_mode is True

        assert client.post("/api/mode", json={"mode": "nope"}).status_code == 400


def test_agent_stream_round_trips_permission(web, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, _store = web
    import core.agent_service as agent_service

    monkeypatch.setattr(agent_service, "AgentOrchestrator", FakeOrchestrator)

    with _paired_client(base, server) as client:
        session_id = client.post("/api/sessions").json()["id"]
        events: list[dict] = []
        with client.stream(
            "POST",
            "/api/agent",
            json={"session_id": session_id, "message": "write a file"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                events.append(event)
                if event["type"] == "permission_request":
                    resolved = client.post(
                        f"/api/permissions/{event['id']}",
                        json={"decision": "allow"},
                    )
                    assert resolved.status_code == 200

        types = [event["type"] for event in events]
        assert types[0] == "start"
        assert "run" in types
        assert "permission_request" in types
        assert any(
            event["type"] == "text" and event["content"] == "decision=allow"
            for event in events
        )
        assert "complete" in types

        # The user turn is persisted even though the fake stops early.
        assert server.store.messages(session_id)[0]["content"] == "write a file"


def test_agent_stream_injects_web_context(web, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, _store = web
    import core.agent_service as agent_service
    from core.agent import AgentEvent

    seen_kwargs: list[dict] = []

    class RecordingOrchestrator:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            seen_kwargs.append(kwargs)

        async def run(self, messages):  # type: ignore[no-untyped-def]
            yield AgentEvent("complete", "ok")

    monkeypatch.setattr(agent_service, "AgentOrchestrator", RecordingOrchestrator)

    with _paired_client(base, server) as client:
        session_id = client.post("/api/sessions").json()["id"]
        response = client.post(
            "/api/agent",
            json={"session_id": session_id, "message": "latest news?", "context": "WEB CONTEXT"},
        )
        assert response.status_code == 200

        assert seen_kwargs[0]["extra_context"] == "WEB CONTEXT"


def test_unknown_permission_and_abort(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        missing = client.post("/api/permissions/nope", json={"decision": "allow"})
        assert missing.status_code == 404
        aborted = client.post("/api/agent/abort", json={"run_id": "nope"})
        assert aborted.json()["ok"] is False


def test_agent_settings_endpoints(web, tmp_path: Path) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        config = client.get("/api/permissions/config")
        assert config.status_code == 200
        data = config.json()
        assert data["mode"] == "auto"
        assert data["rules"] == []

        # Switching mode persists and validates.
        switched = client.post("/api/permissions/config", json={"mode": "ask"})
        assert switched.status_code == 200
        assert switched.json()["mode"] == "ask"
        assert client.post("/api/permissions/config", json={"mode": "nope"}).status_code == 400

        # Adding a rule stores the action:resource:effect string.
        added = client.post(
            "/api/permissions/config",
            json={"add": "x", "action": "bash", "resource": "*git push*", "effect": "ask"},
        )
        assert added.status_code == 200
        assert added.json()["rules"] == ["bash:*git push*:ask"]
        assert client.post(
            "/api/permissions/config", json={"add": "x", "effect": "maybe"}
        ).status_code == 400

        reset = client.post("/api/permissions/config", json={"reset": True})
        assert reset.status_code == 200
        assert reset.json()["rules"] == []

        mode = client.post("/api/provider_mode", json={"mode": "native"})
        assert mode.status_code == 200
        assert mode.json()["provider_mode"] == "native"
        assert client.post("/api/provider_mode", json={"mode": "bogus"}).status_code == 400

        ctx = client.post("/api/context", json={"limit": 4096})
        assert ctx.status_code == 200
        assert ctx.json()["context_max"] == 4096
        clamped = client.post("/api/context", json={"limit": 10})
        assert clamped.json()["context_max"] == 2_000
        assert client.post("/api/context", json={"limit": "wat"}).status_code == 400

        settings = client.post(
            "/api/agent/settings", json={"workspace": str(tmp_path), "max_steps": 3}
        )
        assert settings.status_code == 200
        assert settings.json() == {"ok": True, "workspace": str(tmp_path), "max_steps": 3}
        info = client.get("/api/info").json()
        assert info["max_steps"] == 3
        assert info["workspace"] == str(tmp_path)


def test_service_status_and_actions(web) -> None:
    server, base, _store = web
    with _paired_client(base, server) as client:
        status = client.get("/api/mcp/status")
        assert status.status_code == 200
        assert "MCP" in status.json()["status"]

        skills = client.get("/api/skills")
        assert skills.status_code == 200
        assert isinstance(skills.json()["summary"], str)

        lsp = client.get("/api/lsp/status")
        assert lsp.status_code == 200
        assert "LSP" in lsp.json()["status"]

        # Unknown actions are rejected before touching the runtime loop.
        assert client.post("/api/mcp", json={"action": "explode"}).status_code == 400
        assert client.post("/api/lsp", json={"action": "stop"}).status_code == 200
        assert client.post("/api/skills", json={"action": "reload"}).status_code == 200


def test_research_endpoints(web, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, _store = web

    async def fake_rewrite(message, history, llm, region="India"):
        return message

    monkeypatch.setattr("core.research.rewrite_query", fake_rewrite)
    with _paired_client(base, server) as client:
        # Smalltalk never triggers the search pipeline.
        small = client.post("/api/research/prepare", json={"message": "hi there!"})
        assert small.status_code == 200
        assert small.json()["search"] is False

        async def fake_decide(question, llm):  # type: ignore[no-untyped-def]
            return True, ["latest gemma news", question]

        def fake_search(query, **kwargs):  # type: ignore[no-untyped-def]
            return [
                {"title": "Gemma release notes", "url": "https://example.com/gemma", "engine": "searxng"},
            ]

        async def async_search(query, **kwargs):  # type: ignore[no-untyped-def]
            return fake_search(query, **kwargs)

        monkeypatch.setattr("core.orchestrator.decide_search", fake_decide)
        monkeypatch.setattr("core.research.web_search", async_search)

        prep = client.post(
            "/api/research/prepare", json={"message": "what is the latest gemma news?"}
        )
        assert prep.status_code == 200
        data = prep.json()
        assert data["search"] is True
        assert data["query"] == "latest gemma news"
        assert "WEB search results" in data["context"] or "### Web search results" in data["context"]
        assert data["results"] == [
            {"title": "Gemma release notes", "url": "https://example.com/gemma", "engine": "searxng", "date": ""}
        ]

        # Nothing found → search stays true, but the failure is explicit.
        async def empty_search(query, **kwargs):  # type: ignore[no-untyped-def]
            return []

        async def fake_health(base_url):  # type: ignore[no-untyped-def]
            return False, "Connection refused"

        monkeypatch.setattr("core.research.web_search", empty_search)
        monkeypatch.setattr("core.research.check_searxng_health", fake_health)
        empty = client.post("/api/research/prepare", json={"message": "breaking news now"})
        empty_data = empty.json()
        assert empty_data["search"] is True
        assert empty_data["query"] == "latest gemma news"
        assert empty_data["rewritten"] == "latest gemma news"
        assert empty_data["results"] == []
        assert empty_data["healthy"] is False
        assert empty_data["detail"] == "Connection refused"

        # Web context rides into the chat messages after the system prompt.
        messages = server.build_messages("any", "CTX")
        assert messages[1] == {"role": "system", "content": "CTX"}
        assert len(server.build_messages("any")) == 1  # no context → no extra message


def test_compact_endpoint(web, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, store = web
    with _paired_client(base, server) as client:
        assert client.post("/api/compact", json={"session_id": "nope"}).status_code == 404

        session_id = store.create_session()
        fresh = store.create_session()
        for index in range(6):
            store.add_message(session_id, "user", f"question {index} " + "y" * 9000)
            store.add_message(session_id, "assistant", f"answer {index} " + "z" * 9000)

        empty = client.post("/api/compact", json={"session_id": fresh})
        assert empty.status_code == 200
        assert empty.json()["ok"] is False

        async def fake_search_context(query, **kwargs):  # type: ignore[no-untyped-def]
            return ""

        # The compactor calls the real model endpoint; stub the LlamaClient it builds.
        class StubClient:
            async def complete(self, prompt: str, **kwargs):  # type: ignore[no-untyped-def]
                return "## Checkpoint summary"

            async def stream_chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
                yield "ignored"

        monkeypatch.setattr("core.client.LlamaClient", lambda settings: StubClient())
        done = client.post(
            "/api/compact", json={"session_id": session_id, "instructions": "keep the plan"}
        )
        assert done.status_code == 200
        data = done.json()
        assert data["ok"] is True
        assert data["removed"] > 0
        assert data["summary"] == "## Checkpoint summary"
        roles = [row["role"] for row in store.messages(session_id)]
        assert roles[-1] == "system"
        assert store.get_summary(session_id) == "## Checkpoint summary"


def test_init_workspace_endpoint(web, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, _store = web
    monkeypatch.setattr(server.settings, "workspace_dir", str(tmp_path / "ws"))
    with _paired_client(base, server) as client:
        first = client.post("/api/init")
        assert first.status_code == 200
        data = first.json()
        assert data["ok"] is True
        target = Path(data["path"])
        assert target.name == "AGENTS.md"
        assert "## Overview" in target.read_text()

        again = client.post("/api/init")
        assert again.json() == {"ok": False, "path": str(target), "reason": "exists"}


def test_research_fresh_quality(web, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh queries get time windows + news categories, ranked sources, dated
    results, a compact context, and the two-phase prepare/read flow."""
    import core.research as research

    # Fresh detection drives the recency window.
    assert research._is_fresh_query("what is the latest released gemini model")
    assert research._is_fresh_query("current news")
    assert not research._is_fresh_query("write a python loop")
    assert not research._is_fresh_query("hi")

    # Wikipedia is deprioritized, dated results come first.
    ranked = research._rank_results(
        [
            {"title": "Wiki", "url": "https://en.wikipedia.org/wiki/A", "date": ""},
            {"title": "Old news", "url": "https://old.example.com/a", "date": "2024-01-01"},
            {"title": "Fresh", "url": "https://fresh.example.com/b", "date": "2025-11-01"},
        ]
    )
    assert ranked[0]["title"] == "Fresh"
    assert ranked[-1]["title"] == "Wiki"

    # SearXNG params carry the window and categories.
    params = research._searxng_params("latest gemini", "month", "news,general")
    assert params["time_range"] == "month"
    assert params["categories"] == "news,general"
    assert "time_range" not in research._searxng_params("hello")

    # The context prompt carries today's date, recency and honesty rules, and
    # stays under ~2000 tokens.
    context = research.format_context(
        [
            {
                "title": "Fresh news",
                "url": "https://news.example.com/a",
                "date": "2025-11-01",
                "snippet": "short snippet",
                "excerpt": "x" * 9000,
            }
        ]
    )
    assert "Today is" in context
    assert "most recent" in context.lower()
    assert "fetched page text" in context
    assert "Start with the answer" in context
    assert "(Source:" not in context
    assert "Published: 2025-11-01" in context
    assert len(context) <= research._CONTEXT_CHAR_BUDGET + 700  # instructions overhead

    server, base, _store = web
    with _paired_client(base, server) as client:
        async def fake_decide(question, llm):  # type: ignore[no-untyped-def]
            return True, ["latest gemini model", question]

        async def fake_search(query, **kwargs):  # type: ignore[no-untyped-def]
            return [
                {"title": "Gemini model news", "url": "https://news.example.com/a", "engine": "searxng", "date": "2025-11-01"},
            ]

        monkeypatch.setattr("core.orchestrator.decide_search", fake_decide)
        monkeypatch.setattr("core.research.web_search", fake_search)

        # Phase 1 (deep_read=false) returns results only — fast "searching".
        prep = client.post(
            "/api/research/prepare",
            json={"message": "what is the latest released gemini model", "deep_read": False},
        )
        data = prep.json()
        assert data["search"] is True
        assert data["results"][0]["date"] == "2025-11-01"
        assert "context" not in data

        # Phase 2 deep-reads the pages in parallel and builds the context.
        async def fake_enrich(results, client, ua, deep_read=True):  # type: ignore[no-untyped-def]
            return [{**results[0], "excerpt": "page text"}]

        monkeypatch.setattr("core.research._enrich", fake_enrich)
        read = client.post(
            "/api/research/read",
            json={"query": "latest gemini model", "results": data["results"]},
        )
        read_data = read.json()
        assert read_data["ok"] is True
        assert "page text" in read_data["context"]
        assert read_data["results"][0]["excerpt"] == "page text"
        assert read_data["results"][0]["date"] == "2025-11-01"
