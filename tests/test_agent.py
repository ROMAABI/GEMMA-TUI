from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from config.settings import Settings
from core.agent import AgentOrchestrator
from core.permissions import PermissionManager
from core.providers import ContextOverflowError, ProviderEvent, ToolCall
from core.tooling import ToolRegistry, ToolResult, ToolSpec
from core.tools import WorkspaceTools
from sessions.store import SessionStore


class FakeProvider:
    def __init__(self, scripts: list[list[ProviderEvent]]) -> None:
        self.scripts = scripts
        self.calls = 0
        self.last_provider = "fake"

    async def stream(self, messages, tools, **kwargs):
        index = min(self.calls, len(self.scripts) - 1)
        self.calls += 1
        for event in self.scripts[index]:
            yield event


def make_agent(tmp_path: Path, scripts, **kwargs) -> tuple[AgentOrchestrator, SessionStore, str, FakeProvider]:
    settings = Settings(
        workspace_dir=str(tmp_path),
        permission_mode="auto",
        context_limit=100_000,
        agent_max_steps=kwargs.pop("max_steps", 6),
    )
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session_id = store.create_session("Test")
    store.add_message(session_id, "user", "do the thing")
    agent = AgentOrchestrator(
        settings=settings,
        tools=WorkspaceTools(root_dir=tmp_path),
        store=store,
        session_id=session_id,
        max_steps=settings.agent_max_steps,
        **kwargs,
    )
    provider = FakeProvider(scripts)
    agent.provider = provider
    return agent, store, session_id, provider


async def collect(agent: AgentOrchestrator, store: SessionStore, session_id: str):
    return [
        event
        async for event in agent.run(store.context_messages(session_id))
    ]


def test_agent_writes_file_and_completes(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent("text", text="Writing the file."),
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write",
                        arguments={"path": "hello.py", "content": "print('hi')\n"},
                    )
                ],
            ),
            ProviderEvent("done", finish_reason="tool_calls"),
        ],
        [ProviderEvent("text", text="Done: created hello.py."), ProviderEvent("done")],
    ]
    agent, store, session_id, _ = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))

    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
    types = [event.type for event in events]
    assert "tool_start" in types and "tool_finish" in types
    assert events[-1].type == "complete"
    assert events[-1].content == "Done: created hello.py."

    stored = store.messages(session_id)
    roles = [message["role"] for message in stored]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert stored[1]["metadata"]["tool_calls"][0]["id"] == "c1"
    assert stored[2]["tool_call_id"] == "c1"


def test_agent_handles_multiple_tools_in_order(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("AAA\n")
    (tmp_path / "b.txt").write_text("BBB\n")
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="read", arguments={"path": "a.txt"}),
                    ToolCall(id="c2", name="read", arguments={"path": "b.txt"}),
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Both read."), ProviderEvent("done")],
    ]
    agent, store, session_id, _ = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))
    finishes = [e for e in events if e.type == "tool_finish"]
    assert [e.content["id"] for e in finishes] == ["c1", "c2"]
    tool_messages = [m for m in store.messages(session_id) if m["role"] == "tool"]
    assert "AAA" in tool_messages[0]["content"]
    assert "BBB" in tool_messages[1]["content"]


def test_agent_denied_permission_does_not_run_tool(tmp_path: Path) -> None:
    async def deny(request):
        return "deny"

    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="bash",
                        arguments={"command": "touch denied.txt"},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Could not run the command."), ProviderEvent("done")],
    ]
    permission = PermissionManager(mode="ask", approval=deny)
    agent, store, session_id, _ = make_agent(
        tmp_path, scripts, permission=permission
    )
    events = asyncio.run(collect(agent, store, session_id))
    assert not (tmp_path / "denied.txt").exists()
    finish = [e for e in events if e.type == "tool_finish"][0]
    assert not finish.content["ok"]
    assert "denied" in finish.content["result"].lower()


def test_agent_reports_malformed_arguments(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="write", arguments={}, error="bad JSON")
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Recovered."), ProviderEvent("done")],
    ]
    agent, store, session_id, _ = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))
    finish = [e for e in events if e.type == "tool_finish"][0]
    assert "bad JSON" in finish.content["result"]
    # The model did not acknowledge the failed write, so the final answer is
    # annotated instead of silently presenting "Recovered." as success.
    assert "Completion not verified" in events[-1].content
    assert "Recovered." in events[-1].content


def test_agent_unknown_tool_is_reported(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c1", name="nope", arguments={})],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Unknown tool."), ProviderEvent("done")],
    ]
    agent, store, session_id, _ = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))
    finish = [e for e in events if e.type == "tool_finish"][0]
    assert "Unknown tool" in finish.content["result"]


def test_agent_max_steps_finalizes(tmp_path: Path) -> None:
    call = ProviderEvent(
        "tool_calls",
        tool_calls=[ToolCall(id="c1", name="list_dir", arguments={"path": "."})],
    )
    scripts = [
        [call, ProviderEvent("done")],
        [call, ProviderEvent("done")],
        [ProviderEvent("text", text="Step limit summary."), ProviderEvent("done")],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts, max_steps=2)
    events = asyncio.run(collect(agent, store, session_id))
    assert events[-1].type == "complete"
    assert events[-1].content == "Step limit summary."
    assert provider.calls == 3


def test_agent_context_overflow_compacts_and_retries(tmp_path: Path) -> None:
    class Compactor:
        def __init__(self):
            self.calls = 0

        def should_compact(self, *args, **kwargs):
            return False

        async def compact(self, messages, reason="auto"):
            self.calls += 1
            from core.compaction import CompactionResult

            return CompactionResult(
                messages=[{"role": "system", "content": "summary"}, *messages[-1:]],
                summary="summary",
                removed=1,
                before_tokens=100,
                after_tokens=50,
                reason=reason,
            )

    class OverflowOnce(FakeProvider):
        async def stream(self, messages, tools, **kwargs):
            if self.calls == 0:
                self.calls += 1
                raise ContextOverflowError("too long")
            self.calls += 1
            yield ProviderEvent("text", text="Recovered from overflow.")
            yield ProviderEvent("done")

    agent, store, session_id, _ = make_agent(tmp_path, [])
    compactor = Compactor()
    agent.compactor = compactor
    provider = OverflowOnce([])
    agent.provider = provider
    events = asyncio.run(collect(agent, store, session_id))
    assert compactor.calls == 1
    assert events[-1].content == "Recovered from overflow."
    assert store.get_summary(session_id) == "summary"
    assert any(
        m["role"] == "system" and (m.get("metadata") or {}).get("kind") == "checkpoint"
        for m in store.messages(session_id)
    )


def test_cancel_mid_tool_persists_matching_tool_message(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c1", name="slow", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderEvent("done", finish_reason="tool_calls"),
        ]
    ]
    agent, store, session_id, _ = make_agent(tmp_path, scripts)

    async def slow(context, args):
        await asyncio.sleep(30)
        return ToolResult.success("finished")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow",
            description="Never finishes in time",
            parameters={"type": "object", "properties": {}},
            handler=slow,
            permission="read",
        )
    )
    agent.registry = registry
    agent.permission = PermissionManager(mode="auto")

    started = asyncio.Event()

    async def consume() -> None:
        async for event in agent.run(store.context_messages(session_id)):
            if event.type == "tool_start":
                started.set()

    async def run() -> None:
        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    messages = store.messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "c1"
    assert "did not complete" in messages[-1]["content"]


def test_agent_stops_after_repeated_identical_calls(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    call = ToolCall(id="c1", name="read", arguments={"path": "notes.txt"})
    script = [
        ProviderEvent(
            "tool_calls",
            tool_calls=[call],
            finish_reason="tool_calls",
        ),
        ProviderEvent("done", finish_reason="tool_calls"),
    ]
    agent, store, session_id, _ = make_agent(tmp_path, [script], max_steps=10)

    events = asyncio.run(collect(agent, store, session_id))

    warnings = [event for event in events if event.type == "loop_warning"]
    assert warnings
    assert warnings[0].content["repeat_count"] == 3
    # Four identical rounds, then a loop-stop summary instead of 10 rounds.
    tool_messages = [m for m in store.messages(session_id) if m["role"] == "tool"]
    assert len(tool_messages) == 4
    assert events[-1].type == "complete"
    assert "repeated without making progress" in events[-1].content


def test_agent_nudges_success_claim_after_blocking_failure(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="bash", arguments={"command": "false"})
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Task completed."), ProviderEvent("done")],
        [
            ProviderEvent(
                "text",
                text="I could not complete the task: the command failed with exit code 1.",
            ),
            ProviderEvent("done"),
        ],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 3
    assert any(event.type == "completion_warning" for event in events)
    assert "could not complete" in events[-1].content
    assert events[-1].content != "Task completed."


def test_agent_empty_final_after_failure_returns_honest_summary(tmp_path: Path) -> None:
    empty = [ProviderEvent("text", text=""), ProviderEvent("done")]
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="bash", arguments={"command": "false"})
                ],
            ),
            ProviderEvent("done"),
        ],
        empty,
        empty,
        empty,
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 4
    assert "could not confirm" in events[-1].content
    assert "bash" in events[-1].content
    assert "Task completed." not in events[-1].content


def test_agent_accepts_failure_acknowledgement_without_retry(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="bash", arguments={"command": "false"})
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "text",
                text="I could not complete the task because the command failed.",
            ),
            ProviderEvent("done"),
        ],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 2
    assert not any(event.type == "completion_warning" for event in events)
    assert events[-1].content.startswith("I could not complete")


def test_agent_nonblocking_read_failure_does_not_block_completion(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="read", arguments={"path": "missing.txt"})
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Done: created config.py."), ProviderEvent("done")],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 2
    assert not any(event.type == "completion_warning" for event in events)
    assert events[-1].content == "Done: created config.py."


def test_agent_resolves_failure_after_same_command_succeeds(tmp_path: Path) -> None:
    check = ToolCall(id="c1", name="bash", arguments={"command": "test -f marker"})
    scripts = [
        [
            ProviderEvent("tool_calls", tool_calls=[check]),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="write",
                        arguments={"path": "marker", "content": "ok\n"},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c3", name="bash", arguments={"command": "test -f marker"})
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "text",
                text="Task completed: created marker and verified it with test -f.",
            ),
            ProviderEvent("done"),
        ],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 4
    assert not any(event.type == "completion_warning" for event in events)
    assert events[-1].content.startswith("Task completed")


def test_agent_rechecks_when_files_change_after_verification(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write",
                        arguments={"path": "hello.py", "content": 'print("ORANGE-42")\n'},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c2", name="bash", arguments={"command": "python hello.py"})
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c3",
                        name="write",
                        arguments={"path": "hello.py", "content": "ORANGE-42\n"},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="The task is complete."), ProviderEvent("done")],
        [
            ProviderEvent(
                "text",
                text="I re-ran the script and it now fails; the task is not complete.",
            ),
            ProviderEvent("done"),
        ],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts, max_steps=8)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 5
    warnings = [event for event in events if event.type == "completion_warning"]
    assert any(event.content.get("reason") == "stale_verification" for event in warnings)
    assert "not complete" in events[-1].content


def test_agent_annotates_stale_success_after_retry(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write",
                        arguments={"path": "hello.py", "content": 'print("ORANGE-42")\n'},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(id="c2", name="bash", arguments={"command": "python hello.py"})
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c3",
                        name="write",
                        arguments={"path": "hello.py", "content": "ORANGE-42\n"},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [ProviderEvent("text", text="Task completed."), ProviderEvent("done")],
        [ProviderEvent("text", text="Task completed."), ProviderEvent("done")],
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts, max_steps=8)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 5
    assert "Completion not verified" in events[-1].content
    assert "files were changed after the last successful check" in events[-1].content


def test_success_claim_negation_is_local() -> None:
    assert AgentOrchestrator._claims_success(
        "No further steps are required. The task is complete."
    )
    assert AgentOrchestrator._claims_success("Task completed.")
    assert not AgentOrchestrator._claims_success("The task is not completed.")
    assert not AgentOrchestrator._claims_success("The task was never finished.")


def test_agent_warmup_uses_its_system_prompt_and_tools(tmp_path: Path) -> None:
    agent, _store, _session, _provider = make_agent(tmp_path, [])
    seen: dict[str, object] = {}

    async def fake_warmup(messages, tools):
        seen["messages"] = messages
        seen["tools"] = tools
        return True

    agent.provider.warmup = fake_warmup  # type: ignore[attr-defined]
    assert asyncio.run(agent.warmup()) is True

    messages = seen["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == agent.system_prompt
    assert messages[1]["role"] == "user"
    assert [tool["function"]["name"] for tool in seen["tools"]] == [
        spec.name for spec in agent.registry.specs()
    ]


def test_agent_warmup_is_best_effort(tmp_path: Path) -> None:
    agent, _store, _session, _provider = make_agent(tmp_path, [])

    async def broken_warmup(messages, tools):
        raise RuntimeError("server down")

    agent.provider.warmup = broken_warmup  # type: ignore[attr-defined]
    assert asyncio.run(agent.warmup()) is False


def _text_script(text: str) -> list[ProviderEvent]:
    return [ProviderEvent("text", text=text), ProviderEvent("done")]


def test_corrected_bash_command_clears_the_earlier_failure(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "rm d"})],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c2", name="bash", arguments={"command": "rm -r d"})],
            ),
            ProviderEvent("done"),
        ],
        _text_script("The directory `d` has been removed."),
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts, max_steps=8)
    events = asyncio.run(collect(agent, store, session_id))

    assert provider.calls == 3
    assert not (tmp_path / "d").exists()
    assert not any(event.type == "completion_warning" for event in events)
    assert "Completion not verified" not in events[-1].content


def test_unrelated_success_does_not_clear_a_bash_failure(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="bash",
                        arguments={"command": "mkdir /definitely/not/here/x"},
                    )
                ],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c2", name="bash", arguments={"command": "ls ."})],
            ),
            ProviderEvent("done"),
        ],
        _text_script("The task is complete."),
    ]
    agent, store, session_id, _provider = make_agent(tmp_path, scripts, max_steps=8)
    events = asyncio.run(collect(agent, store, session_id))

    assert "Completion not verified" in events[-1].content


def test_alternating_tool_calls_stop_instead_of_running_to_the_limit(tmp_path: Path) -> None:
    def call(name: str, cid: str) -> list[ProviderEvent]:
        return [
            ProviderEvent(
                "tool_calls", tool_calls=[ToolCall(id=cid, name="bash", arguments={"command": name})]
            ),
            ProviderEvent("done"),
        ]

    scripts = [call("ls", f"c{i}") if i % 2 == 0 else call("pwd", f"c{i}") for i in range(6)]
    scripts.append(_text_script("Done looking around."))
    agent, store, session_id, provider = make_agent(tmp_path, scripts, max_steps=10)
    events = asyncio.run(collect(agent, store, session_id))

    warnings = [e for e in events if e.type == "loop_warning"]
    assert warnings
    assert provider.calls < 10


def test_redundant_reads_after_a_change_stop_the_loop(tmp_path: Path) -> None:
    def call(command: str, cid: str) -> list[ProviderEvent]:
        return [
            ProviderEvent(
                "tool_calls", tool_calls=[ToolCall(id=cid, name="bash", arguments={"command": command})]
            ),
            ProviderEvent("done"),
        ]

    scripts = [
        call("mkdir made", "c1"),
        call("ls", "c2"),
        call("ls -l", "c3"),
        call("ls -la", "c4"),
        call("ls -a", "c5"),
        call("ls -lh", "c6"),
        _text_script("Created the directory."),
    ]
    agent, store, session_id, provider = make_agent(tmp_path, scripts, max_steps=10)
    events = asyncio.run(collect(agent, store, session_id))

    warnings = [e for e in events if e.type == "loop_warning"]
    assert any(e.content.get("reason") == "redundant_verification" for e in warnings)
    assert provider.calls < 10
    assert (tmp_path / "made").is_dir()


def test_reads_before_any_change_are_not_treated_as_redundant(tmp_path: Path) -> None:
    scripts = [
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "ls"})],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c2", name="bash", arguments={"command": "ls -l"})],
            ),
            ProviderEvent("done"),
        ],
        [
            ProviderEvent(
                "tool_calls",
                tool_calls=[ToolCall(id="c3", name="bash", arguments={"command": "mkdir made"})],
            ),
            ProviderEvent("done"),
        ],
        _text_script("Created the directory."),
    ]
    agent, store, session_id, _provider = make_agent(tmp_path, scripts, max_steps=10)
    events = asyncio.run(collect(agent, store, session_id))

    assert not any(
        e.type == "loop_warning" and e.content.get("reason") == "redundant_verification"
        for e in events
    )
    assert (tmp_path / "made").is_dir()


def test_mutating_call_detection() -> None:
    ok = ToolResult.success("")
    assert AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="bash", arguments={"command": "rm -r x"}), ok
    )
    assert AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="bash", arguments={"command": "git commit -m wip"}), ok
    )
    assert AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="bash", arguments={"command": "pip install requests"}), ok
    )
    assert not AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="bash", arguments={"command": "git status"}), ok
    )
    assert not AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="bash", arguments={"command": "ls -la"}), ok
    )
    assert not AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="bash", arguments={"command": "rm x"}), ToolResult.failure("nope")
    )
    assert AgentOrchestrator._is_mutating_call(
        ToolCall(id="1", name="write", arguments={"path": "a.txt", "content": "x"}), ok
    )
