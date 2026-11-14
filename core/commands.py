from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    name: str
    args: str


COMMAND_DEFS: list[tuple[str, str]] = [
    ("/help", "Show commands and shortcuts"),
    ("/model", "Pick or switch the active model"),
    ("/theme", "Switch color theme"),
    ("/clear", "Clear the visible chat"),
    ("/new", "Start a new session"),
    ("/delete", "Delete current session"),
    ("/history", "Show saved sessions"),
    ("/copy", "Copy last reply"),
    ("/stats", "Show current session stats"),
    ("/rename <name>", "Rename current session"),
    ("/sidebar [show|hide|toggle]", "Toggle or show/hide sidebar navbar (Ctrl+B)"),
    ("/mode [chat|agent]", "Toggle between Chat and Autonomous Coding Agent mode"),
    ("/agent [on|off|status|dir <path>|steps <n>]", "Configure the coding agent"),
    ("/provider [auto|native|prompt]", "Choose native tool calling or XML fallback"),
    ("/permissions [status|mode|allow|deny|reset]", "Inspect or change permission rules"),
    ("/compact [instructions]", "Compact the conversation into a checkpoint"),
    ("/context [limit]", "Show or set the context token limit"),
    ("/mcp [status|start|reload|stop]", "Manage MCP servers and tools"),
    ("/skills [list|reload]", "List or reload skills"),
    ("/lsp [status|stop]", "Inspect or stop language servers"),
    ("/init", "Create a starter AGENTS.md in the workspace"),
    ("/web", "Open the web UI in a browser (Ctrl+W)"),
    ("/websearch [on|off|status|test <q>|url <url>]", "Configure & test SearXNG web search"),
    ("/exit", "Quit the application"),
]


HELP_TEXT = """# Commands

`/help` - Show commands and shortcuts
`/sidebar [show|hide|toggle]` - Toggle or show/hide sidebar navbar (Ctrl+B)
`/mode [chat|agent]` - Toggle Chat Mode vs Autonomous Coding Agent Mode
`/agent [on|off|status|dir <path>|steps <n>]` - Configure the coding agent
`/provider [auto|native|prompt]` - Native OpenAI tool calling, XML fallback, or automatic
`/permissions [status|mode|allow|deny|reset]` - Inspect or change permission rules
`/compact [instructions]` - Compact the conversation into a structured checkpoint
`/context [limit]` - Show or set the context token limit
`/mcp [status|start|reload|stop]` - Manage MCP servers and tools
`/skills [list|reload]` - List or reload skills
`/lsp [status|stop]` - Inspect or stop language servers
`/init` - Create a starter `AGENTS.md` in the workspace
`/model [name]` - Pick or switch the active model (click the header model too)
`/theme [name]` - Switch color theme (system, tokyo-night, deep-purple, dracula, nord, cyberpunk, emerald, opencode)
`/clear` - Clear the visible chat
`/new` - Start a new session
`/delete` - Delete current session
`/history` - Show saved sessions
`/copy` - Copy last reply
`/stats` - Show current session stats
`/rename <name>` - Rename current session
`/config` - Show config
`/web` - Open the web UI in a browser (pairing link, shared sessions)
`/websearch [on|off|status|test <q>|url <url>]` - Configure & test SearXNG web search
`/exit` - Quit

## Agent Tools

`read`, `write`, `edit`, `glob`, `grep`, `list_dir`, `bash`, `patch`,
`git_status`, `git_diff`, `git_log`, `git_commit`, `webfetch`,
`lsp_diagnostics`, `lsp_hover`, `lsp_definition`, `lsp_references`,
`lsp_symbols`, `skill`, `skills_list`, and any connected MCP tools.

## Shortcuts

`Ctrl+B` - Toggle sidebar navbar
`Ctrl+N` - New chat
`Ctrl+L` - Clear chat
`Ctrl+C` - Cancel streaming
`Ctrl+Y` - Copy last reply
`Ctrl+W` - Open the web UI
`Ctrl+D` - Exit
`Delete` - Delete current session

Web search only triggers when the question needs current/up-to-date information
(e.g. recent news, new tech releases, current office holders).
"""


def parse_command(text: str) -> CommandResult | None:
    if not text.startswith("/"):
        return None
    command, _, args = text[1:].partition(" ")
    return CommandResult(command.strip().lower(), args.strip())
