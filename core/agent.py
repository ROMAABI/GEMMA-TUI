from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Awaitable

from core.client import LlamaClient
from core.tools import (
    AGENT_SYSTEM_PROMPT,
    WorkspaceTools,
    format_tool_result,
    parse_tool_calls,
)


@dataclass
class AgentEvent:
    type: str  # "thought", "tool_start", "tool_finish", "complete", "error"
    content: Any  # str or dict with details


class AgentOrchestrator:
    """Executes a multi-turn ReAct coding agent loop with local tools and model."""

    def __init__(
        self,
        client: LlamaClient,
        tools: WorkspaceTools,
        max_steps: int = 10,
        confirm_commands: bool = False,
    ) -> None:
        self.client = client
        self.tools = tools
        self.max_steps = max_steps
        self.confirm_commands = confirm_commands

    async def execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Dispatch tool name to WorkspaceTools implementation."""
        try:
            if name == "read_file":
                path = args.get("path", "")
                s = args.get("start_line") or args.get("start")
                e = args.get("end_line") or args.get("end")
                return await asyncio.to_thread(self.tools.read_file, path, s, e)
            elif name == "write_file":
                path = args.get("path", "")
                content = args.get("content", "")
                return await asyncio.to_thread(self.tools.write_file, path, content)
            elif name == "edit_file":
                path = args.get("path", "")
                old_str = args.get("old_str") or args.get("old_content") or args.get("old", "")
                new_str = args.get("new_str") or args.get("new_content") or args.get("new", "")
                return await asyncio.to_thread(self.tools.edit_file, path, old_str, new_str)
            elif name == "list_dir":
                path = args.get("path", ".")
                depth = int(args.get("max_depth", 2))
                return await asyncio.to_thread(self.tools.list_dir, path, depth)
            elif name == "grep_search":
                query = args.get("query") or args.get("pattern", "")
                path = args.get("path", ".")
                return await asyncio.to_thread(self.tools.grep_search, query, path)
            elif name == "run_command":
                cmd = args.get("command") or args.get("cmd", "")
                timeout = int(args.get("timeout", 30))
                return await self.tools.run_command(cmd, timeout=timeout)
            else:
                return f"Error: Unknown tool '{name}'. Available: read_file, write_file, edit_file, list_dir, grep_search, run_command"
        except Exception as exc:
            return f"Error executing '{name}': {exc}"

    async def run(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[AgentEvent]:
        """Run the autonomous ReAct agent loop."""
        # Prepare conversation history with AGENT_SYSTEM_PROMPT
        conv: list[dict[str, str]] = []
        if messages and messages[0].get("role") == "system":
            conv.append({"role": "system", "content": AGENT_SYSTEM_PROMPT})
            conv.extend(messages[1:])
        else:
            conv.append({"role": "system", "content": AGENT_SYSTEM_PROMPT})
            conv.extend(messages)

        step = 0
        while step < self.max_steps:
            step += 1
            step_output = ""

            try:
                async for chunk in self.client.stream_chat(conv):
                    step_output += chunk
                    yield AgentEvent(type="thought", content=chunk)
            except Exception as exc:
                yield AgentEvent(type="error", content=f"Model stream error: {exc}")
                return

            tool_calls = parse_tool_calls(step_output)

            # If no tools called, the task is finished
            if not tool_calls:
                yield AgentEvent(type="complete", content=step_output)
                return

            # Append model's step output to conversation
            conv.append({"role": "assistant", "content": step_output})

            # Execute each tool call
            observations: list[str] = []
            for name, args in tool_calls:
                yield AgentEvent(type="tool_start", content={"name": name, "args": args, "step": step})
                result = await self.execute_tool(name, args)
                yield AgentEvent(type="tool_finish", content={"name": name, "args": args, "result": result, "step": step})
                observations.append(format_tool_result(name, result))

            # Feed observations back to conversation for the next iteration
            obs_text = "\n\n".join(observations)
            conv.append({"role": "user", "content": obs_text})

        yield AgentEvent(
            type="complete",
            content=f"Completed {self.max_steps} maximum agent steps. Summary of actions taken above.",
        )
