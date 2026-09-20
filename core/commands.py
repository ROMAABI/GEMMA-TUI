from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    name: str
    args: str


COMMAND_DEFS: list[tuple[str, str]] = [
    ("/help", "Show commands and shortcuts"),
    ("/model", "Show or switch model alias"),
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
    ("/agent [on|off|status|dir <path>]", "Configure Coding Agent workspace and settings"),
    ("/websearch [on|off|status|test <q>|url <url>]", "Configure & test SearXNG web search"),
    ("/exit", "Quit the application"),
]


HELP_TEXT = """# Commands

`/help` - Show commands and shortcuts
`/sidebar [show|hide|toggle]` - Toggle or show/hide sidebar navbar (Ctrl+B)
`/mode [chat|agent]` - Toggle Chat Mode vs Autonomous Coding Agent Mode
`/agent [on|off|status|dir <path>]` - Configure Coding Agent (tools: read, write, edit, bash)
`/model [name]` - Show or switch model alias
`/theme [name]` - Switch color theme (system, tokyo-night, deep-purple, dracula, nord, cyberpunk, emerald)
`/clear` - Clear the visible chat
`/new` - Start a new session
`/delete` - Delete current session
`/history` - Show saved sessions
`/copy` - Copy last reply
`/stats` - Show current session stats
`/rename <name>` - Rename current session
`/config` - Show config
`/websearch [on|off|status|test <q>|url <url>]` - Configure & test SearXNG web search
`/exit` - Quit

## Shortcuts

`Ctrl+B` - Toggle sidebar navbar
`Ctrl+N` - New chat
`Ctrl+L` - Clear chat
`Ctrl+C` - Cancel streaming
`Ctrl+Y` - Copy last reply
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
