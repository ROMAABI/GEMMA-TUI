# OpenCode-Inspired Agent Architecture

This document records the architecture research and the mapping used to evolve
Gemma TUI from a prompt/XML ReAct loop into a reliable, extensible coding agent.
It is a design record, not a claim that every item is already complete. See
`IMPLEMENTATION_PROGRESS.md` for live status.

## Reference

- Source reference: `/home/spix/research/opencode-reference` (OpenCode v2.0.28)
- Installed binary: `/usr/bin/opencode` (v2.2.5, Bun-compiled ELF)
- No deep binary provider (Ghidra/IDA/Hopper/Kuna) is configured, so behavior
  was taken from source plus binary string/runtime inspection.

Key reference modules:

| Concern | Reference |
| --- | --- |
| Step orchestration | `packages/core/src/session/runner/llm.ts` |
| Stream + tool execution | `packages/core/src/session/runner/step.ts` |
| Permissions | `packages/core/src/permission.ts` |
| Tool plugins | `packages/core/src/tool/plugin/*` |
| Patch format | `packages/core/src/tool/plugin/patch.ts` |
| Compaction | `packages/core/src/session/compaction.ts` |
| MCP | `packages/core/src/mcp/client.ts`, `tool/mcp.ts` |
| Skills | `packages/core/src/skill/*` |
| Agent definitions | `packages/core/src/agent/*` |

## Core Patterns Adopted

1. **One LLM call per step.** Durable history is reloaded each step; tool
   results are appended as structured messages. The loop never "keeps thinking"
   inside a single stream.
2. **Native tool calling first, prompt fallback second.** OpenAI-compatible
   `/v1/chat/completions` with `tools` is preferred. If the local server or
   model cannot do native tools, fall back to the existing XML protocol.
3. **Tools are first-class objects.** Each tool owns a JSON schema, a permission
   action, a handler, aliases, and an output budget. The registry produces the
   provider schema and dispatches calls.
4. **Permissions are evaluated last-match-wins.** Rules match an action
   (`read`, `edit`, `bash`, `webfetch`, `mcp`) against a resource glob. The
   default is `ask`; explicit allow/deny rules override.
5. **Structured events.** Streaming text, reasoning, tool start/finish,
   permission requests, usage, compaction, and completion are distinct events so
   the TUI can render them independently.
6. **Durable sessions.** Messages store tool call IDs, names, and JSON metadata.
   Events are also recorded for replay/audit.
7. **Compaction is a checkpoint, not a truncation.** Older history is replaced
   by a structured summary plus a recent verbatim window.
8. **MCP tools are namespaced.** `mcp__<server>__<tool>` avoids collisions and
   makes provenance obvious.
9. **Skills are progressive disclosure.** Only name/description are in the
   prompt; full instructions load when the model invokes the `skill` tool.

## Component Mapping

| OpenCode | Gemma TUI implementation |
| --- | --- |
| `LLMClient` / provider route | `core/providers.py` |
| `session/runner/step.ts` | `core/agent.py` (`AgentOrchestrator`) |
| `tool/registry` | `core/tooling/registry.py` |
| `tool/plugin/read|write|edit|glob|grep` | `core/tooling/filesystem.py` |
| `tool/plugin/shell` | `core/tooling/shell.py` |
| `tool/plugin/patch` | `core/tooling/patch.py` |
| Git helpers | `core/tooling/git.py` |
| `permission.ts` | `core/permissions.py` |
| `session/compaction.ts` | `core/compaction.py` |
| `mcp/client.ts` | `core/mcp.py` |
| LSP (not yet in reference) | `core/lsp.py` |
| `skill/*` | `core/skills.py` |
| Session store | `sessions/store.py` |
| TUI event rendering | `tui/app.py` (`ToolStepCard`, permission modal) |

## Compatibility Contract

The existing public surface is preserved:

- `LlamaClient.complete()` and `LlamaClient.stream_chat()` keep working for
  plain chat and web-search judging.
- `WorkspaceTools` remains importable and keeps its method signatures.
- `parse_tool_calls()` / `format_tool_result()` remain importable.
- `AgentOrchestrator.run(messages)` keeps yielding `AgentEvent` objects, with
  additional event types and richer payloads.
- `SessionStore` keeps its existing methods and gains optional metadata
  arguments.

## Implementation Order

1. Provider abstraction + native tool calling.
2. Tool framework + core tools (read/write/edit/glob/grep/bash/patch/git).
3. Permission manager + approval flow.
4. Hardened agent loop (parallel tools, malformed output recovery, cancellation).
5. Session schema migration + event persistence.
6. Context compaction.
7. MCP, LSP, skills.
8. TUI integration (tool cards, permission modal, commands, status).
9. Tests and live smoke tests against llama.cpp.
