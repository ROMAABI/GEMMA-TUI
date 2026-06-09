from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    name: str
    args: str


COMMAND_DEFS: list[tuple[str, str]] = [
    ("/help", "Show commands and shortcuts"),
    ("/model", "Show or switch model alias"),
    ("/clear", "Clear the visible chat"),
    ("/new", "Start a new session"),
    ("/delete", "Delete current session"),
    ("/history", "Show saved sessions"),
    ("/stats", "Show current session stats"),
    ("/rename <name>", "Rename current session"),
    ("/config", "Show configuration"),
    ("/exit", "Quit the application"),
]


HELP_TEXT = """# Commands

`/help` - Show commands and shortcuts
`/model [name]` - Show or switch model alias
`/clear` - Clear the visible chat
`/new` - Start a new session
`/delete` - Delete current session
`/history` - Show saved sessions
`/stats` - Show current session stats
`/rename <name>` - Rename current session
`/config` - Show config
`/exit` - Quit

# Shortcuts

`Ctrl+N` - New chat
`Ctrl+L` - Clear chat
`Ctrl+C` - Cancel streaming
`Ctrl+D` - Exit
`Delete` - Delete current session
"""


def parse_command(text: str) -> CommandResult | None:
    if not text.startswith("/"):
        return None
    command, _, args = text[1:].partition(" ")
    return CommandResult(command.strip().lower(), args.strip())
