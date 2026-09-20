from __future__ import annotations

import asyncio
import getpass
import os
import re
import subprocess
import time
from datetime import datetime

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import MountError, Widget
from textual.widgets import Input, Label, ListItem, ListView, Static, TextArea

from app.lifecycle import async_server_is_healthy, async_get_loaded_model
from config.settings import Settings
from core.agent import AgentOrchestrator, AgentEvent
from core.client import LlamaClient
from core.commands import COMMAND_DEFS, HELP_TEXT, parse_command
from core.orchestrator import decide_search
from core.research import search_context, web_search, check_searxng_health
from core.titles import suggest_title
from core.tools import WorkspaceTools, AGENT_SYSTEM_PROMPT
from sessions.store import SessionStore, format_session_timestamp
from tui.themes import THEMES, DEFAULT_THEME, Theme, get_theme, generate_all_themes_css


def _load_unfiltered_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "UNFILTERED_SYSTEM_PROMPT.md")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        if "---" not in lines:
            return "\n".join(lines).strip()
        return "\n".join(lines[lines.index("---") + 1 :]).strip()
    except OSError:
        return "You are a helpful assistant. Reply directly without thinking or reasoning steps."


SYSTEM_PROMPT = _load_unfiltered_prompt()


def _get_username() -> str:
    username = Settings.load().username
    return username or getpass.getuser().capitalize()


def _get_greeting() -> str:
    """Return a time-appropriate greeting."""
    hour = datetime.now().hour
    if hour < 12:
        return "Morning"
    elif hour < 17:
        return "Afternoon"
    elif hour < 21:
        return "Evening"
    return "Night"


# ── Custom Widgets ──────────────────────────────────────────────────────


class ChatMessage(Static):
    """A single chat message bubble with avatar, name, timestamp, and content."""

    def __init__(
        self,
        role: str,
        content: str,
        *,
        elapsed: float | None = None,
        timestamp: str | None = None,
        syntax_theme: str = "monokai",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.role = role
        self.msg_content = content
        self.elapsed = elapsed
        self.msg_timestamp = timestamp or datetime.now().strftime("%I:%M %p")
        self.syntax_theme = syntax_theme
        self.add_class(f"role-{role}")

    def compose(self) -> ComposeResult:
        username = _get_username()
        if self.role == "user":
            with Vertical(classes="user-msg-card"):
                with Horizontal(classes="user-msg-header"):
                    yield Static("❯", classes="user-avatar")
                    yield Static(username, classes="user-name")
                    yield Static(self.msg_timestamp, classes="msg-time")
                    yield Static("", classes="header-spacer")
                    yield Static("❐ Copy", classes="msg-copy")
                yield Static(self.msg_content, classes="msg-text", markup=False)
        else:
            with Vertical(classes="assistant-msg-box"):
                with Horizontal(classes="assistant-msg-header"):
                    yield Static("◆", classes="assistant-avatar")
                    yield Static("Gemma", classes="assistant-name")
                    yield Static(self.msg_timestamp, classes="msg-time")
                    yield Static("", classes="header-spacer")
                    yield Static("❐ Copy", classes="msg-copy")
                if "```" in self.msg_content:
                    parts = self.msg_content.split("```")
                    for i, part in enumerate(parts):
                        if i % 2 == 0:
                            s = part.strip()
                            if s:
                                yield Static(Markdown(s), classes="msg-text")
                        else:
                            lines = part.split("\n", 1)
                            lang = lines[0].strip() or "text"
                            code = lines[1].rstrip("\n") if len(lines) > 1 else ""
                            yield CodeBlock(code, language=lang, theme_name=self.syntax_theme)
                else:
                    yield Static(Markdown(self.msg_content), classes="msg-text")
                if self.elapsed is not None:
                    yield Static(f"⚡ {self.elapsed:.1f}s", classes="elapsed-badge")


class ToolStepCard(Static):
    """Widget displaying an agent tool execution step."""

    def __init__(
        self,
        tool_name: str,
        args: dict[str, Any],
        step: int = 1,
        status: str = "running",
        result: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.args = args
        self.step = step
        self.status = status
        self.result = result

    def _arg_summary(self) -> str:
        if self.tool_name in ("read_file", "write_file", "edit_file"):
            p = self.args.get("path", "")
            if self.tool_name == "read_file":
                s = self.args.get("start_line") or self.args.get("start")
                e = self.args.get("end_line") or self.args.get("end")
                if s and e:
                    return f"{p}:{s}-{e}"
            return p
        elif self.tool_name == "run_command":
            return self.args.get("command", "")[:45]
        elif self.tool_name == "grep_search":
            q = self.args.get("query") or self.args.get("pattern", "")
            return f"'{q}'"
        elif self.tool_name == "list_dir":
            return self.args.get("path", ".")
        return str(self.args)[:40]

    def compose(self) -> ComposeResult:
        with Vertical(classes="tool-step-card"):
            with Horizontal(classes="tool-step-header"):
                icon = "⏳" if self.status == "running" else ("✓" if self.status == "done" else "✗")
                yield Static(icon, classes=f"tool-icon tool-status-{self.status}", id="step-icon")
                yield Static(f"Step {self.step}: {self.tool_name}", classes="tool-name")
                yield Static(self._arg_summary(), classes="tool-target")
            if self.result:
                preview = self.result.strip()
                if len(preview) > 300:
                    preview = preview[:300] + "..."
                yield Static(preview, classes="tool-output-preview")

    def update_status(self, status: str, result: str = "") -> None:
        self.status = status
        self.result = result
        try:
            icon_widget = self.query_one("#step-icon", Static)
            icon = "⏳" if status == "running" else ("✓" if status == "done" else "✗")
            icon_widget.update(icon)
            icon_widget.set_classes(f"tool-icon tool-status-{status}")
            if result:
                preview = result.strip()
                if len(preview) > 300:
                    preview = preview[:300] + "..."
                previews = self.query(".tool-output-preview")
                if previews:
                    previews.first().update(preview)
                else:
                    self.query_one(".tool-step-card", Vertical).mount(
                        Static(preview, classes="tool-output-preview")
                    )
        except Exception:
            pass


class LandingInput(Input):
    """Input widget for the landing screen that handles autocomplete keys."""

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        if getattr(app, "_autocomplete_visible", False):
            if event.key == "down":
                event.stop()
                event.prevent_default()
                app._autocomplete_cursor_down()
                return
            if event.key == "up":
                event.stop()
                event.prevent_default()
                app._autocomplete_cursor_up()
                return
            if event.key == "tab":
                event.stop()
                event.prevent_default()
                app._accept_autocomplete(submit_if_no_args=False)
                return
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                app._accept_autocomplete(submit_if_no_args=True)
                return
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                app._hide_autocomplete()
                return
        await super()._on_key(event)


class ChatInput(TextArea):
    """TextArea that submits on Enter, inserts newline on Shift+Enter."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("placeholder", "Ask Gemma anything... (type / for commands)")
        kwargs.setdefault("highlight_cursor_line", False)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("soft_wrap", True)
        super().__init__(*args, **kwargs)

    async def _on_key(self, event) -> None:
        app = self.app
        if getattr(app, "_autocomplete_visible", False):
            if event.key == "down":
                event.stop()
                event.prevent_default()
                app._autocomplete_cursor_down()
                return
            if event.key == "up":
                event.stop()
                event.prevent_default()
                app._autocomplete_cursor_up()
                return
            if event.key == "tab":
                event.stop()
                event.prevent_default()
                app._accept_autocomplete(submit_if_no_args=False)
                return
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                app._accept_autocomplete(submit_if_no_args=True)
                return
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                app._hide_autocomplete()
                return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


class CodeBlock(Static):
    """A code block with language label, syntax highlighting, and copy button."""

    def __init__(self, code: str, language: str = "python", theme_name: str = "monokai", **kwargs) -> None:
        super().__init__(**kwargs)
        self.code = code
        self.language = language
        self.theme_name = theme_name

    def compose(self) -> ComposeResult:
        with Vertical(classes="code-block"):
            with Horizontal(classes="code-header"):
                yield Static(f" {self.language} ˅", classes="code-lang")
                yield Static("❐ Copy", classes="code-copy")
            try:
                bg_col = "default" if self.theme_name in ("ansi_dark", "ansi_light", "default") else None
                syn = Syntax(self.code, self.language, theme=self.theme_name, background_color=bg_col, word_wrap=True)
                yield Static(syn, classes="code-content")
            except Exception:
                yield Static(self.code, classes="code-content", markup=False)


class AutoCompleteItem(ListItem):
    """ListItem that carries a command name for autocomplete."""
    def __init__(self, cmd: str, desc: str) -> None:
        self.command = cmd
        super().__init__(
            Horizontal(
                Static(f"  {cmd}", classes="autocomplete-key"),
                Static(desc, classes="autocomplete-desc"),
            )
        )


class ThemeItem(ListItem):
    """ListItem displaying a theme in the ThemePicker dialog."""
    def __init__(self, theme: Theme, active: bool = False) -> None:
        self.theme_id = theme.id
        marker = "● " if active else "  "
        color_label = "terminal" if theme.id == "system" else theme.border_accent
        super().__init__(
            Horizontal(
                Static(f" {marker}{theme.name}", classes="theme-name"),
                Static(color_label, classes="theme-color"),
            )
        )


class ThemePicker(ModalScreen[str | None]):
    """Modal overlay to browse and select themes."""

    DEFAULT_CSS = """
    ThemePicker {
        align: center top;
        background: rgba(10, 14, 23, 0.85);
    }
    #theme-dialog {
        width: 1fr;
        max-width: 50;
        height: auto;
        margin-top: 6;
        background: #111A2E;
        border: solid #38BDF8;
        padding: 1 2;
    }
    #theme-title {
        text-style: bold;
        color: #38BDF8;
        height: 2;
        content-align: center middle;
    }
    #theme-list {
        height: auto;
        max-height: 12;
        background: transparent;
        border: none;
        padding: 0;
    }
    #theme-list > ListItem {
        height: 2;
        padding: 0 1;
        color: #E2E8F0;
    }
    #theme-list > ListItem:hover,
    #theme-list > ListItem.--highlight {
        background: #162038;
        color: #38BDF8;
    }
    .theme-name {
        width: 1fr;
        text-style: bold;
    }
    .theme-color {
        width: auto;
        color: #94A3B8;
    }
    """

    def __init__(self, current_theme_id: str) -> None:
        super().__init__()
        self.current_theme_id = current_theme_id

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-dialog"):
            yield Static("Select Visual Theme", id="theme-title")
            yield ListView(id="theme-list")

    def on_mount(self) -> None:
        try:
            self.add_class(f"theme-{self.current_theme_id}")
        except Exception:
            pass
        lst = self.query_one("#theme-list", ListView)
        lst.clear()
        active_idx = 0
        for i, (tid, theme) in enumerate(THEMES.items()):
            active = tid == self.current_theme_id
            if active:
                active_idx = i
            lst.append(ThemeItem(theme, active=active))
        if lst.children:
            lst.index = active_idx
        lst.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ThemeItem):
            self.dismiss(event.item.theme_id)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "down":
            event.stop()
            self.query_one("#theme-list", ListView).action_cursor_down()
        elif event.key == "up":
            event.stop()
            self.query_one("#theme-list", ListView).action_cursor_up()


class CommandItem(ListItem):
    """ListItem that carries a command name."""
    def __init__(self, cmd: str, desc: str) -> None:
        self.command = cmd
        super().__init__(
            Horizontal(
                Static(f"  {cmd}", classes="command-key"),
                Static(desc, classes="command-desc"),
                Static("⏎", classes="command-arrow"),
            )
        )


class CommandPalette(ModalScreen[str | None]):
    """Modal overlay for searching and executing commands."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center top;
        background: rgba(10, 14, 23, 0.85);
    }
    #command-palette {
        width: 1fr;
        max-width: 64;
        height: auto;
        margin-top: 6;
        background: #111A2E;
        border: solid #38BDF8;
        padding: 0;
    }
    #command-search {
        height: 3;
        background: #0A101D;
        border: none;
        color: #F1F5F9;
        padding: 0 1;
    }
    #command-search:focus {
        border: none;
    }
    #command-list {
        height: auto;
        max-height: 16;
        background: #111A2E;
        border: none;
        padding: 0;
        margin: 0;
    }
    #command-list > ListItem {
        height: 2;
        padding: 0 1;
        color: #F1F5F9;
    }
    #command-list > ListItem:hover,
    #command-list > ListItem.--highlight {
        background: #162038;
    }
    .command-key {
        color: #38BDF8;
        text-style: bold;
        width: auto;
    }
    .command-desc {
        color: #94A3B8;
        width: 1fr;
    }
    .command-arrow {
        color: #64748B;
        width: auto;
    }
    """

    commands: list[tuple[str, str]] = COMMAND_DEFS

    def compose(self) -> ComposeResult:
        with Vertical(id="command-palette"):
            yield Input(placeholder="Search commands...", id="command-search")
            yield ListView(id="command-list")

    def on_mount(self) -> None:
        try:
            self.add_class(f"theme-{self.app.active_theme.id}")
        except Exception:
            pass
        self._populate_commands()
        self.query_one("#command-search", Input).focus()

    def _populate_commands(self, filter_text: str = "") -> None:
        lst = self.query_one("#command-list", ListView)
        lst.clear()
        for cmd, desc in self.commands:
            if filter_text.lower() in cmd.lower() or filter_text.lower() in desc.lower():
                lst.append(CommandItem(cmd, desc))
        if lst.children:
            lst.index = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command-search":
            self._populate_commands(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "command-list":
            self.dismiss(event.item.command if isinstance(event.item, CommandItem) else None)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "down":
            event.stop()
            self.query_one("#command-list", ListView).action_cursor_down()
        elif event.key == "up":
            event.stop()
            self.query_one("#command-list", ListView).action_cursor_up()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-search":
            event.stop()
            lst = self.query_one("#command-list", ListView)
            if lst.highlighted_child and isinstance(lst.highlighted_child, CommandItem):
                self.dismiss(lst.highlighted_child.command)
            elif lst.children and isinstance(lst.children[0], CommandItem):
                self.dismiss(lst.children[0].command)
            else:
                self.dismiss(None)


# ── Main TUI Application ───────────────────────────────────────────────

BASE_CSS = """
Screen {
    overflow: hidden;
}

* {
    scrollbar-size-vertical: 1;
}

#shell {
    height: 1fr;
    width: 1fr;
}

#sidebar {
    width: 30;
    min-width: 26;
    max-width: 32;
    height: 1fr;
    padding: 1 1 0 1;
}

#sidebar.hidden {
    display: none;
}

#nav-toggle {
    width: 3;
    height: 1;
    content-align: center middle;
    margin-right: 1;
    text-style: bold;
}

#nav-toggle:hover {
    text-style: bold underline;
}

#sidebar-header {
    height: auto;
    padding: 0 1;
    margin-bottom: 1;
}

#brand {
    text-style: bold;
    height: 1;
}

#brand-sub {
    height: 1;
}

.nav-section-title {
    text-style: bold;
    padding: 0 1;
    height: 1;
    margin-top: 1;
    margin-bottom: 0;
}

#nav-new {
    height: 3;
    padding: 0 1;
    margin: 1 0;
    content-align: left middle;
    text-style: bold;
}

#nav-chats {
    height: 3;
    padding: 0 1;
    margin: 0 0;
    content-align: left middle;
}

#sessions {
    height: 1fr;
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
}

#sessions > ListItem {
    height: 3;
    padding: 0 1;
    border: none;
    margin-bottom: 0;
}

.session-item-row {
    width: 1fr;
    height: 1;
    align-vertical: middle;
}

.session-title {
    width: 1fr;
}

.session-time {
    width: auto;
    text-style: none;
}

#nav-settings {
    height: 3;
    padding: 0 1;
    content-align: left middle;
}

#workspace {
    width: 1fr;
    height: 1fr;
}

#topbar {
    height: 3;
    padding: 0 2;
    align-vertical: middle;
}

#topbar-left {
    width: 1fr;
    height: 1;
    align-vertical: middle;
}

#topbar-model {
    width: auto;
    text-style: bold;
}

#topbar-right {
    width: auto;
    height: 1;
    align-vertical: middle;
}

#model-status {
    width: auto;
    margin-right: 1;
    text-style: bold;
}

.topbar-divider {
    width: auto;
    margin: 0 1;
}

#topbar-time {
    width: auto;
    margin: 0 1;
}

#topbar-help {
    width: auto;
    text-style: bold;
    padding: 0 1;
}

#topbar-mode {
    width: auto;
    text-style: bold;
    padding: 0 1;
    margin-right: 1;
}

#topbar-mode:hover {
    text-style: bold underline;
}

.tool-step-card {
    width: 1fr;
    height: auto;
    padding: 0 1;
    margin-bottom: 1;
}

.tool-step-header {
    width: 1fr;
    height: 1;
    align-vertical: middle;
}

.tool-icon {
    width: 3;
    text-style: bold;
}

.tool-name {
    width: auto;
    text-style: bold;
    margin-right: 1;
}

.tool-target {
    width: 1fr;
    text-style: italic;
}

.tool-output-preview {
    margin-top: 0;
    padding: 0 1;
    height: auto;
    max-height: 4;
}

#landing {
    height: 1fr;
    align: center middle;
}

#landing-inner {
    width: 1fr;
    max-width: 84;
    height: auto;
    align: center middle;
    padding: 0 1;
}

#landing-brand-box {
    width: 1fr;
    height: auto;
    align: center middle;
    margin-bottom: 2;
}

#landing-brand-title {
    text-style: bold;
    height: 1;
    content-align: center middle;
}

#landing-sub {
    margin-top: 1;
    content-align: center middle;
}

#landing-suggestions {
    width: 1fr;
    max-width: 84;
    height: auto;
    min-height: 3;
    margin-bottom: 2;
    align: center middle;
}

.sug-card {
    width: 1fr;
    min-width: 16;
    height: 3;
    margin: 0 1;
    padding: 0 1;
    align-vertical: middle;
}

.sug-num {
    width: 3;
    height: 1;
    content-align: center middle;
    text-style: bold;
    margin-right: 1;
}

.sug-text {
    width: 1fr;
    text-style: bold;
}

#prompt-card {
    width: 1fr;
    height: 4;
    padding: 0 1;
    align-vertical: middle;
}

#prompt {
    width: 1fr;
    height: 3;
    background: transparent;
    border: none;
}

#prompt:focus {
    border: none;
}

.send-btn {
    width: auto;
    height: 3;
    content-align: center middle;
    padding: 0 2;
    text-style: bold;
}

#chat-area {
    height: 1fr;
    width: 1fr;
}

#chat-scroll {
    height: 1fr;
    padding: 1 3;
}

#chat-messages {
    width: 1fr;
    height: auto;
}

.user-msg-card {
    width: 1fr;
    height: auto;
    padding: 1 2;
    margin-bottom: 1;
}

.user-msg-header {
    width: 1fr;
    height: 1;
    margin-bottom: 1;
    align-vertical: middle;
}

.user-avatar {
    width: 3;
    height: 1;
    content-align: center middle;
    text-style: bold;
    margin-right: 1;
}

.user-name {
    width: auto;
    text-style: bold;
    margin-right: 1;
}

.assistant-msg-box {
    width: 1fr;
    height: auto;
    padding: 1 1;
    margin-bottom: 1;
}

.assistant-msg-header {
    width: 1fr;
    height: 1;
    margin-bottom: 1;
    align-vertical: middle;
}

.assistant-avatar {
    width: 2;
    height: 1;
    content-align: center middle;
    text-style: bold;
    margin-right: 1;
}

.assistant-name {
    width: auto;
    text-style: bold;
    margin-right: 1;
}

.msg-time {
    width: auto;
    margin-right: 1;
}

.header-spacer {
    width: 1fr;
}

.msg-copy {
    width: auto;
    padding: 0 1;
}

.msg-text {
    width: 1fr;
}

.elapsed-badge {
    width: auto;
    height: 1;
    margin-top: 1;
    text-style: italic;
}

.code-block {
    width: 1fr;
    height: auto;
    margin: 1 0;
}

.code-header {
    width: 1fr;
    height: 1;
    padding: 0 1;
    align-vertical: middle;
}

.code-lang {
    width: 1fr;
    text-style: bold;
}

.code-copy {
    width: auto;
}

.code-content {
    padding: 1 2;
}

#input-area {
    height: 7;
    padding: 0 3 1 3;
}

#input-card {
    width: 1fr;
    height: 4;
    padding: 0 1;
    align-vertical: middle;
}

#chat-prompt {
    width: 1fr;
    height: 3;
    background: transparent;
    border: none;
    padding: 0;
}

#chat-prompt:focus {
    border: none;
}

.input-footer {
    width: 1fr;
    height: 1;
    margin-top: 1;
    align-vertical: middle;
}

.footer-left {
    width: 1fr;
    height: 1;
    align-vertical: middle;
}

.footer-cmd {
    width: auto;
    margin-right: 2;
    text-style: bold;
}

.footer-hints {
    width: auto;
    text-align: right;
}

#statusbar {
    height: 1;
    padding: 0 2;
    content-align: left middle;
}

.autocomplete-popup {
    padding: 0;
    margin: 0;
    display: none;
}

.autocomplete-popup.visible {
    display: block;
}

#landing-autocomplete {
    width: 1fr;
    max-width: 84;
    max-height: 10;
    margin-top: 1;
}

#chat-autocomplete {
    width: 1fr;
    margin: 0 3;
    max-height: 8;
}

.autocomplete-list {
    height: auto;
    max-height: 8;
    border: none;
    padding: 0;
    margin: 0;
}

.autocomplete-list > ListItem {
    height: 2;
    padding: 0 1;
}

.autocomplete-key {
    text-style: bold;
    width: auto;
}

.autocomplete-desc {
    width: 1fr;
}

#command-palette {
    width: 1fr;
    max-width: 64;
    height: auto;
    margin-top: 6;
    padding: 0;
}

#command-search {
    height: 3;
    border: none;
    padding: 0 1;
}

#command-search:focus {
    border: none;
}

#command-list {
    height: auto;
    max-height: 16;
    border: none;
    padding: 0;
    margin: 0;
}

#command-list > ListItem {
    height: 2;
    padding: 0 1;
}

.command-key {
    text-style: bold;
    width: auto;
}

.command-desc {
    width: 1fr;
}

.command-arrow {
    width: auto;
}

#streaming-indicator {
    width: 1fr;
    height: auto;
    margin-top: 1;
    text-style: italic;
}

.hidden {
    display: none;
}

.responsive-compact #topbar {
    padding: 0 1;
}

.responsive-compact #topbar-time {
    display: none;
}

.responsive-compact .topbar-divider-time {
    display: none;
}

.responsive-compact #sug-3 {
    display: none;
}

.responsive-compact .footer-hints {
    display: none;
}

.responsive-compact #chat-scroll {
    padding: 1 1;
}

.responsive-compact .user-msg-card {
    padding: 1 1;
}

.responsive-compact #input-card {
    margin: 0 1;
}
"""


def _detect_screen_columns() -> int:
    """Detect full screen column capacity from window manager or fallback to 160."""
    try:
        res = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if res.returncode == 0:
            import json
            monitors = json.loads(res.stdout)
            if monitors and isinstance(monitors, list):
                mon = next((m for m in monitors if m.get("focused")), monitors[0])
                w = mon.get("width", 1920)
                scale = float(mon.get("scale", 1.0))
                eff_w = w / scale
                cols = int(eff_w / 9.0)
                if cols >= 80:
                    return cols
    except Exception:
        pass
    return 160


class GemmaTUI(App):
    CSS = BASE_CSS + "\n" + generate_all_themes_css()

    BINDINGS = [
        ("ctrl+b", "toggle_sidebar", "Navbar"),
        ("ctrl+n", "new_session", "New"),
        ("ctrl+l", "clear_chat", "Clear"),
        ("ctrl+c", "cancel_stream", "Cancel"),
        ("ctrl+d", "quit", "Exit"),
        ("ctrl+q", "quit", "Quit"),
        ("delete", "delete_session", "Delete"),
        ("ctrl+p", "command_palette", "Commands"),
        ("ctrl+t", "theme_picker", "Theme"),
        ("ctrl+y", "copy_last_reply", "Copy"),
        ("escape", "close_autocomplete", ""),
        ("ctrl+enter", "submit_chat", "Send"),
    ]

    commands: list[tuple[str, str]] = COMMAND_DEFS

    model_loaded = reactive(True)
    is_streaming = reactive(False)
    token_count = reactive(0)
    gen_count = reactive(0)
    gen_time = reactive(0.0)
    context_used = reactive(0)
    context_max = reactive(8192)

    def __init__(self, settings: Settings, initial_session_id: str | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.store = SessionStore()
        self.client = LlamaClient(settings)
        self.session_id = self.store.ensure_session(initial_session_id)
        self.streaming_task: asyncio.Task[None] | None = None
        self.started_at = time.monotonic()
        self.output_buffer = ""
        self.total_tokens = 0
        self.total_gen_count = 0
        self._autocomplete_visible = False
        self._autocomplete_index = 0
        self._pending_mounts: list[tuple[Vertical, Widget]] = []
        self._pending_title: str | None = None
        self.websearch_enabled = True
        self.active_theme = get_theme(self.settings.theme)
        self._screen_cols: int = _detect_screen_columns()
        self._sidebar_visible: bool = True
        self._last_below_60: bool | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="shell"):
            # ── Sidebar ──
            with Vertical(id="sidebar"):
                with Vertical(id="sidebar-header"):
                    yield Static("◆  GEMMA TUI", id="brand")
                    yield Static("Local AI, In Your Terminal", id="brand-sub")
                yield Static("NAVIGATION", id="nav-title", classes="nav-section-title")
                yield Static("+  New Chat", id="nav-new")
                yield Static("💬  Chats", id="nav-chats")
                yield Static("RECENT CHATS", id="recents-title", classes="nav-section-title")
                yield ListView(id="sessions")
                yield Static("⚙  Settings", id="nav-settings")

            # ── Workspace ──
            with Vertical(id="workspace"):
                # Top bar
                with Horizontal(id="topbar"):
                    with Horizontal(id="topbar-left"):
                        yield Static("☰", id="nav-toggle", classes="nav-toggle-btn")
                        yield Static(f"Model: {self.settings.model} ˅", id="topbar-model")
                    with Horizontal(id="topbar-right"):
                        yield Static(
                            "🤖 AGENT" if self.settings.agent_mode else "💬 CHAT",
                            id="topbar-mode",
                            classes="topbar-mode-agent" if self.settings.agent_mode else "topbar-mode-chat",
                        )
                        yield Static("│", classes="topbar-divider")
                        yield Static("●  CONNECTED", id="model-status")
                        yield Static("│", classes="topbar-divider topbar-divider-time")
                        yield Static("", id="topbar-time")
                        yield Static("│", classes="topbar-divider")
                        yield Static("?", id="topbar-help")

                # Landing page
                with Container(id="landing"):
                    with Vertical(id="landing-inner"):
                        with Vertical(id="landing-brand-box"):
                            yield Static(f"◆  GEMMA TUI", id="landing-brand-title")
                            yield Static("Local AI, ready when you are.", id="landing-sub")
                        
                        with Horizontal(id="landing-suggestions"):
                            with Horizontal(id="sug-1", classes="sug-card"):
                                yield Static("1", classes="sug-num")
                                yield Static("Explain a concept", classes="sug-text")
                            with Horizontal(id="sug-2", classes="sug-card"):
                                yield Static("2", classes="sug-num")
                                yield Static("Write a function", classes="sug-text")
                            with Horizontal(id="sug-3", classes="sug-card"):
                                yield Static("3", classes="sug-num")
                                yield Static("Help me plan", classes="sug-text")

                        with Horizontal(id="prompt-card"):
                            yield LandingInput(
                                placeholder="Ask Gemma anything... (type / for commands)",
                                id="prompt",
                            )
                            yield Static("✈ Send", id="landing-send-btn", classes="send-btn")

                        with Horizontal(id="prompt-footer", classes="input-footer"):
                            with Horizontal(id="footer-left", classes="footer-left"):
                                yield Static("/new", id="cmd-new", classes="footer-cmd cmd-new")
                                yield Static("/mode", id="cmd-mode", classes="footer-cmd cmd-mode")
                                yield Static("/model", id="cmd-model", classes="footer-cmd cmd-model")
                                yield Static("/theme", id="cmd-theme", classes="footer-cmd cmd-theme")
                                yield Static("/rename", id="cmd-rename", classes="footer-cmd cmd-rename")
                                yield Static("/delete", id="cmd-delete", classes="footer-cmd cmd-delete")
                                yield Static("/clear", id="cmd-clear", classes="footer-cmd cmd-clear")
                                yield Static("/help", id="cmd-help", classes="footer-cmd cmd-help")
                            yield Static("↑↓ Navigate  •  Enter Send  •  Ctrl+L Clear  •  Ctrl+Q Quit", id="footer-hints", classes="footer-hints")

                        with Vertical(id="landing-autocomplete", classes="autocomplete-popup"):
                            yield ListView(id="landing-autocomplete-items", classes="autocomplete-list")

                # Chat area (hidden by default)
                with Vertical(id="chat-area", classes="hidden"):
                    with VerticalScroll(id="chat-scroll"):
                        yield Vertical(id="chat-messages")
                    with Vertical(id="chat-autocomplete", classes="autocomplete-popup"):
                        yield ListView(id="chat-autocomplete-items", classes="autocomplete-list")
                    yield Static("", id="statusbar")
                    with Container(id="input-area"):
                        with Horizontal(id="input-card"):
                            yield ChatInput(
                                id="chat-prompt",
                            )
                            yield Static("✈ Send", id="send-btn", classes="send-btn")
                        with Horizontal(id="chat-footer", classes="input-footer"):
                            with Horizontal(classes="footer-left"):
                                yield Static("/new", classes="footer-cmd cmd-new")
                                yield Static("/mode", classes="footer-cmd cmd-mode")
                                yield Static("/model", classes="footer-cmd cmd-model")
                                yield Static("/theme", classes="footer-cmd cmd-theme")
                                yield Static("/rename", classes="footer-cmd cmd-rename")
                                yield Static("/delete", classes="footer-cmd cmd-delete")
                                yield Static("/clear", classes="footer-cmd cmd-clear")
                                yield Static("/help", classes="footer-cmd cmd-help")
                            yield Static("↑↓ Navigate  •  Enter Send  •  Ctrl+L Clear  •  Ctrl+Q Quit", classes="footer-hints")

    def on_mount(self) -> None:
        self.title = self.settings.title
        self.screen.add_class(f"theme-{self.active_theme.id}")
        self._session_ids: list[str] = []
        self.refresh_sessions()
        self.load_current_session()
        self.update_status_bar()
        self._update_mode_display()
        self._apply_responsive_layout(self.size.width, self.size.height)
        self._app_ready = True
        self.query_one("#prompt", LandingInput).focus()
        asyncio.create_task(self.check_server_health())
        self.set_interval(30, self.check_server_health)
        self._flush_timer = self.set_interval(0.05, self._flush_pending_mounts)
        self._update_clock()
        self.set_interval(1, self._update_clock)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width, event.size.height)

    def _apply_responsive_layout(self, width: int, height: int) -> None:
        if width <= 0:
            import shutil
            width = shutil.get_terminal_size((160, 40)).columns

        if width > self._screen_cols:
            self._screen_cols = width

        threshold = self._screen_cols * 0.6
        is_below_60 = width < threshold

        was_below_60 = self._last_below_60
        if was_below_60 != is_below_60:
            self._last_below_60 = is_below_60
            if is_below_60:
                self.toggle_sidebar(show=False, notify=False)
            else:
                self.toggle_sidebar(show=True, notify=False)

        try:
            if is_below_60 or width < 96:
                self.screen.add_class("responsive-compact")
            else:
                self.screen.remove_class("responsive-compact")
        except Exception:
            pass

    def action_toggle_sidebar(self) -> None:
        """Action for Ctrl+B binding."""
        self.toggle_sidebar()

    def toggle_sidebar(self, show: bool | None = None, notify: bool = True) -> bool:
        """Toggle or explicitly set sidebar/navbar visibility."""
        try:
            sidebar = self.query_one("#sidebar", Vertical)
            if show is None:
                is_hidden = sidebar.has_class("hidden")
                show = is_hidden
            if show:
                sidebar.remove_class("hidden")
                self._sidebar_visible = True
                if notify:
                    self.notify("Navbar shown (Ctrl+B)", timeout=1.5)
            else:
                sidebar.add_class("hidden")
                self._sidebar_visible = False
                if notify:
                    self.notify("Navbar hidden (Ctrl+B)", timeout=1.5)
            self._update_nav_toggle_button()
            return self._sidebar_visible
        except Exception:
            return False

    def _update_nav_toggle_button(self) -> None:
        try:
            btn = self.query_one("#nav-toggle", Static)
            if getattr(self, "_sidebar_visible", True):
                btn.update("☰")
                btn.tooltip = "Hide Navbar (Ctrl+B)"
            else:
                btn.update("☰")
                btn.tooltip = "Show Navbar (Ctrl+B)"
        except Exception:
            pass

    def _update_mode_display(self) -> None:
        try:
            mode_widget = self.query_one("#topbar-mode", Static)
            if self.settings.agent_mode:
                mode_widget.update("🤖 AGENT")
                mode_widget.set_classes("topbar-mode-agent")
            else:
                mode_widget.update("💬 CHAT")
                mode_widget.set_classes("topbar-mode-chat")
            ph = "Ask Gemma to code, edit files, run bash... (type / for commands)" if self.settings.agent_mode else "Ask Gemma anything... (type / for commands)"
            try:
                self.query_one("#prompt", LandingInput).placeholder = ph
            except Exception:
                pass
            try:
                self.query_one("#chat-prompt", ChatInput).placeholder = ph
            except Exception:
                pass
        except Exception:
            pass

    def toggle_mode(self, mode: bool | None = None) -> bool:
        if mode is None:
            self.settings.agent_mode = not self.settings.agent_mode
        else:
            self.settings.agent_mode = mode
        self.settings.save_agent_mode(self.settings.agent_mode)
        self._update_mode_display()
        state_str = "Agent (Autonomous Coding)" if self.settings.agent_mode else "Chat (Standard)"
        self.notify(f"Mode switched to: {state_str}")
        return self.settings.agent_mode

    def _update_clock(self) -> None:
        try:
            now = datetime.now().strftime("%I:%M:%S %p")
            self.query_one("#topbar-time", Static).update(now)
        except Exception:
            pass

    async def check_server_health(self) -> None:
        """Check llama.cpp server status and update indicator without blocking UI."""
        healthy = await async_server_is_healthy(self.settings.base_url)
        self.model_loaded = healthy
        try:
            status_widget = self.query_one("#model-status", Static)
            if healthy:
                status_widget.update("●  CONNECTED")
                status_widget.styles.color = "#22C55E"
                detected = await async_get_loaded_model(self.settings.base_url)
                if detected and detected != self.settings.model:
                    self.settings.model = detected
                    self.settings.save_model(detected)
            else:
                status_widget.update("○  DISCONNECTED")
                status_widget.styles.color = "#EF4444"
            self.update_status_bar()
        except Exception:
            pass

    # ── Theme Management ────────────────────────────────────────────────

    def switch_theme(self, theme_id: str) -> None:
        if theme_id not in THEMES:
            self.notify(f"Unknown theme: {theme_id}", severity="error")
            return
        old_id = self.active_theme.id
        self.active_theme = THEMES[theme_id]
        try:
            self.screen.remove_class(f"theme-{old_id}")
            self.screen.add_class(f"theme-{self.active_theme.id}")
        except Exception:
            pass
        self.settings.save_theme(self.active_theme.id)
        self.notify(f"Theme switched to {self.active_theme.name}")

    def action_theme_picker(self) -> None:
        def on_theme_selected(theme_id: str | None) -> None:
            if theme_id:
                self.switch_theme(theme_id)
        self.push_screen(ThemePicker(self.active_theme.id), on_theme_selected)

    # ── Session Management ──────────────────────────────────────────────

    def refresh_sessions(self) -> None:
        sessions_view = self.query_one("#sessions", ListView)
        sessions_view.clear()
        rows = self.store.list_sessions()
        self._session_ids = [row.id for row in rows]
        active_idx: int | None = None
        items: list[ListItem] = []
        for i, row in enumerate(rows):
            active = row.id == self.session_id
            if active:
                active_idx = i
            marker = "● " if active else "  "
            time_label = format_session_timestamp(row.updated_at)
            clean_title = row.title[:18].strip() or "Untitled Chat"

            item_row = Horizontal(
                Static(f"{marker}{clean_title}", classes="session-title"),
                Static(time_label, classes="session-time"),
                classes="session-item-row",
            )
            li = ListItem(item_row)
            if active:
                li.add_class("active-session")
            items.append(li)
        if items:
            sessions_view.extend(items)
            if active_idx is not None:
                sessions_view.index = active_idx

    def load_current_session(self) -> None:
        container = self.query_one("#chat-messages", Vertical)
        container.remove_children()
        messages = self.store.messages(self.session_id)
        if not messages:
            self.show_landing()
            return

        self.show_chat()
        for message in messages:
            ts_str = None
            if message.get("created_at"):
                try:
                    dt = datetime.fromisoformat(message["created_at"])
                    ts_str = dt.strftime("%I:%M %p")
                except Exception:
                    ts_str = message["created_at"]
            self.write_message(message["role"], message["content"], timestamp=ts_str)
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    # ── Message Display ─────────────────────────────────────────────────

    def write_message(
        self,
        role: str,
        content: str,
        elapsed: float | None = None,
        timestamp: str | None = None,
    ) -> None:
        container = self.query_one("#chat-messages", Vertical)
        msg = ChatMessage(
            role,
            content,
            elapsed=elapsed if role == "assistant" else None,
            timestamp=timestamp,
            syntax_theme=self.active_theme.syntax_theme,
        )
        self._mount_or_queue(container, msg)
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=True)

    def _mount_or_queue(self, parent: Vertical, widget: Widget) -> None:
        """Mount a widget, or queue it if mounting fails during message handling."""
        try:
            parent.mount(widget)
        except MountError:
            self._pending_mounts.append((parent, widget))

    def _flush_pending_mounts(self) -> None:
        """Flush queued mounts from the periodic timer."""
        if not self._pending_mounts:
            return
        remaining: list[tuple[Vertical, Widget]] = []
        for parent, widget in self._pending_mounts:
            try:
                parent.mount(widget)
            except MountError:
                remaining.append((parent, widget))
        self._pending_mounts = remaining

    # ── Input Handling ──────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if getattr(self, "_suppress_autocomplete", False):
            self._suppress_autocomplete = False
            return
        if event.input.id == "prompt":
            val = event.value
            if val.startswith("/") and " " not in val:
                try:
                    self._show_autocomplete(val)
                except Exception:
                    pass
            else:
                try:
                    self._hide_autocomplete()
                except Exception:
                    pass

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if getattr(self, "_suppress_autocomplete", False):
            self._suppress_autocomplete = False
            return
        ta = event.text_area
        if ta.id == "chat-prompt":
            val = ta.text
            if val.startswith("/") and " " not in val:
                try:
                    self._show_autocomplete(val)
                except Exception:
                    pass
            else:
                try:
                    self._hide_autocomplete()
                except Exception:
                    pass

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        self.query_one("#chat-prompt", ChatInput).text = ""
        asyncio.create_task(self._process_input(event.text))

    async def _process_input(self, text: str) -> None:
        self._hide_autocomplete()
        text = text.strip()
        if not text:
            return
        command = parse_command(text)
        if command:
            await self.run_command(command.name, command.args)
            return
        if self.streaming_task and not self.streaming_task.done():
            self.notify("Gemma is still replying. Press Ctrl+C to cancel.", severity="warning")
            if not self.query_one("#landing", Container).has_class("hidden"):
                inp = self.query_one("#prompt", LandingInput)
                if not inp.value:
                    inp.value = text
            else:
                ta = self.query_one("#chat-prompt", ChatInput)
                if not ta.text:
                    ta.text = text
            return
        self.show_chat()
        await asyncio.sleep(0)
        now = datetime.now().strftime("%I:%M %p")
        self.write_message("user", text, timestamp=now)
        is_first_message = self.store.count_messages(self.session_id) == 0
        self.store.add_message(self.session_id, "user", text)
        if is_first_message:
            self._pending_title = text
        self.refresh_sessions()
        self.streaming_task = asyncio.create_task(self.stream_reply())

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._autocomplete_visible:
            self._accept_autocomplete(submit_if_no_args=True)
            return
        text = event.value.strip()
        event.input.value = ""
        await self._process_input(text)

    async def action_submit_chat(self) -> None:
        if not self.query_one("#landing").has_class("hidden"):
            inp = self.query_one("#prompt", LandingInput)
            text = inp.value
            inp.value = ""
            await self._process_input(text)
        else:
            ta = self.query_one("#chat-prompt", ChatInput)
            text = ta.text
            ta.text = ""
            await self._process_input(text)

    def _apply_suggestion(self, text: str) -> None:
        try:
            inp = self.query_one("#prompt", LandingInput)
            inp.value = text
            inp.cursor_position = len(text)
            inp.focus()
        except Exception:
            pass

    def _populate_command_input(self, cmd_prefix: str) -> None:
        if not self.query_one("#landing").has_class("hidden"):
            inp = self.query_one("#prompt", LandingInput)
            inp.value = cmd_prefix
            inp.cursor_position = len(cmd_prefix)
            inp.focus()
        else:
            ta = self.query_one("#chat-prompt", ChatInput)
            ta.text = cmd_prefix
            ta.cursor_location = (0, len(cmd_prefix))
            ta.focus()

    # ── Streaming ───────────────────────────────────────────────────────

    async def stream_reply(self) -> None:
        self.output_buffer = ""
        started = time.monotonic()
        self.is_streaming = True
        self.total_gen_count += 1

        # Show streaming indicator
        container = self.query_one("#chat-messages", Vertical)
        indicator = Static("✦ Gemma is thinking...", id="streaming-indicator")
        container.mount(indicator)
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=True)

        messages_raw = self.store.messages(self.session_id)
        latest_user_text = ""
        for m in reversed(messages_raw):
            if m.get("role") == "user":
                latest_user_text = m.get("content", "").strip().lower()
                break

        # Auto-route coding / filesystem intents to Agent Mode
        is_coding_intent = False
        if latest_user_text:
            coding_patterns = [
                r"\b(write|create|make|code|build|generate|implement)\b.*\b(code|script|file|program|app|game|function|class|module|server|api)\b",
                r"\b(save|write|dump|export|put)\b.*\b(to|into|in)\b.*\b([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_]+)\b",
                r"\b(edit|modify|update|refactor|fix|patch)\b.*\b(file|code|script|[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_]+)\b",
                r"\b(run|execute)\b.*\b(bash|command|shell|terminal|script|test|pytest|python)\b",
                r"\b(snake|pong|tetris|calculator|todo)\b.*\b(game|app|script|program)\b",
            ]
            if any(re.search(p, latest_user_text) for p in coding_patterns):
                is_coding_intent = True

        if self.settings.agent_mode or is_coding_intent:
            await self._stream_agent_reply(container, indicator, scroll, started)
            return

        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.store.messages(
                self.session_id
            )

            if self.websearch_enabled:
                latest_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
                if latest_user:
                    indicator.update("✦ Gemma is thinking...")
                    needs_search, search_queries = await decide_search(
                        latest_user["content"], self.client
                    )
                    if needs_search:
                        indicator.update("✦ Searching the web...")
                        for search_query in search_queries:
                            context = await search_context(
                                search_query,
                                searxng_url=self.settings.searxng_url,
                                deep_read=self.settings.searxng_deep_read,
                            )
                            if context:
                                break
                        if context:
                            messages.insert(1, {"role": "system", "content": context})

            async for chunk in self.client.stream_chat(messages):
                self.output_buffer += chunk
                self.total_tokens += 1
                indicator.update(Text(f"✦ {self.output_buffer}"))
                scroll.scroll_end(animate=False)

            elapsed = max(time.monotonic() - started, 0.001)
            self.gen_time = elapsed
            self.token_count = len(self.output_buffer.split())
            self.context_used = min(
                self.context_used + self.token_count, self.context_max
            )

            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", self.output_buffer, elapsed=elapsed, timestamp=now)
            self.store.add_message(self.session_id, "assistant", self.output_buffer)
            self._kick_off_titling()

        except asyncio.CancelledError:
            try:
                indicator.remove()
            except Exception:
                pass
            if self.output_buffer:
                now = datetime.now().strftime("%I:%M %p")
                self.write_message(
                    "assistant", self.output_buffer + "\n\n*[cancelled]*", timestamp=now
                )
        except Exception as exc:
            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", f"**Error:** `{exc}`", timestamp=now)
        finally:
            self.is_streaming = False
            self.update_status_bar()

    async def _stream_agent_reply(
        self,
        container: Vertical,
        indicator: Static,
        scroll: VerticalScroll,
        started: float,
    ) -> None:
        indicator.update("✦ Initializing Autonomous Coding Agent...")
        scroll.scroll_end(animate=False)

        tools = WorkspaceTools(root_dir=self.settings.workspace_dir)
        orchestrator = AgentOrchestrator(
            client=self.client,
            tools=tools,
            max_steps=self.settings.agent_max_steps,
            confirm_commands=self.settings.agent_confirm_commands,
        )

        messages = self.store.messages(self.session_id)
        active_cards: dict[str, ToolStepCard] = {}
        current_thought = ""
        final_answer = ""

        try:
            async for event in orchestrator.run(messages):
                if event.type == "thought":
                    chunk = str(event.content)
                    current_thought += chunk
                    self.total_tokens += 1
                    preview = current_thought.strip().replace("\n", " ")
                    if len(preview) > 80:
                        preview = "..." + preview[-80:]
                    indicator.update(Text(f"🤖 {preview}"))
                    scroll.scroll_end(animate=False)

                elif event.type == "tool_start":
                    info = event.content
                    step_num = info.get("step", 1)
                    t_name = info.get("name", "")
                    t_args = info.get("args", {})
                    card_key = f"{step_num}_{t_name}"

                    indicator.update(f"⏳ Running {t_name}...")
                    card = ToolStepCard(
                        tool_name=t_name,
                        args=t_args,
                        step=step_num,
                        status="running",
                    )
                    active_cards[card_key] = card
                    try:
                        container.mount(card, before=indicator)
                    except Exception:
                        container.mount(card)
                    scroll.scroll_end(animate=False)
                    current_thought = ""

                elif event.type == "tool_finish":
                    info = event.content
                    step_num = info.get("step", 1)
                    t_name = info.get("name", "")
                    res = str(info.get("result", ""))
                    card_key = f"{step_num}_{t_name}"
                    card = active_cards.get(card_key)
                    if card:
                        status = "error" if res.startswith("Error") else "done"
                        card.update_status(status=status, result=res)
                    indicator.update("✦ Analyzing result...")
                    scroll.scroll_end(animate=False)

                elif event.type == "complete":
                    final_answer = str(event.content)

                elif event.type == "error":
                    final_answer = f"**Agent Error:** `{event.content}`"

            elapsed = max(time.monotonic() - started, 0.001)
            self.gen_time = elapsed

            try:
                indicator.remove()
            except Exception:
                pass

            now = datetime.now().strftime("%I:%M %p")
            clean_reply = final_answer.strip()
            if not clean_reply and current_thought:
                clean_reply = current_thought.strip()
            if not clean_reply:
                clean_reply = "Task completed."

            self.output_buffer = clean_reply
            self.token_count = len(clean_reply.split())
            self.context_used = min(self.context_used + self.token_count, self.context_max)

            self.write_message("assistant", clean_reply, elapsed=elapsed, timestamp=now)
            self.store.add_message(self.session_id, "assistant", clean_reply)
            self._kick_off_titling()

        except asyncio.CancelledError:
            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", "*[Agent task cancelled]*", timestamp=now)
        except Exception as exc:
            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", f"**Agent Exception:** `{exc}`", timestamp=now)
        finally:
            self.is_streaming = False
            self.update_status_bar()

    # ── Auto-title ───────────────────────────────────────────────────────

    def _kick_off_titling(self) -> None:
        source = self._pending_title
        self._pending_title = None
        if source:
            asyncio.create_task(self._auto_title(source))

    async def _auto_title(self, source: str) -> None:
        try:
            title = await suggest_title(self.client, source)
            if title:
                self.store.rename_session(self.session_id, title)
                self.refresh_sessions()
        except Exception:
            pass

    # ── Commands ────────────────────────────────────────────────────────

    async def run_command(self, name: str, args: str) -> None:
        if name == "help":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", HELP_TEXT.strip(), timestamp=now)
        elif name == "model":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            if args.strip():
                self.settings.model = args.strip()
                self.settings.save_model(self.settings.model)
                self.write_message(
                    "assistant",
                    f"Model updated to: `{self.settings.model}`",
                    timestamp=now,
                )
            else:
                from pathlib import Path
                models_dir = Path(__file__).parent.parent / "models"
                local_models = [p.name for p in models_dir.glob("*.gguf")]
                local_list = "\n".join(f"- `{m}`" for m in local_models) if local_models else "None in `models/`"
                self.write_message(
                    "assistant",
                    f"Active model: `{self.settings.model}`\n\n**Local GGUF models:**\n{local_list}\n\nTo change model: `/model <name>`",
                    timestamp=now,
                )
            self.update_status_bar()
        elif name == "theme":
            if args.strip():
                tid = args.strip().lower()
                if tid in THEMES:
                    self.switch_theme(tid)
                else:
                    self.show_chat()
                    await asyncio.sleep(0)
                    available = ", ".join(f"`{k}`" for k in THEMES.keys())
                    now = datetime.now().strftime("%I:%M %p")
                    self.write_message(
                        "assistant",
                        f"Unknown theme `{args.strip()}`. Available themes: {available}",
                        timestamp=now,
                    )
            else:
                self.action_theme_picker()
        elif name == "clear":
            self.query_one("#chat-messages", Vertical).remove_children()
            self.show_landing()
        elif name == "new":
            self.action_new_session()
        elif name == "delete":
            self.action_delete_session()
        elif name == "rename":
            if not args:
                self.show_chat()
                now = datetime.now().strftime("%I:%M %p")
                self.write_message("assistant", "Usage: `/rename <new name>`", timestamp=now)
            else:
                self.store.rename_session(self.session_id, args)
                self.refresh_sessions()
                self.show_chat()
                now = datetime.now().strftime("%I:%M %p")
                self.write_message(
                    "assistant",
                    f"Session renamed to `{args}`",
                    timestamp=now,
                )
        elif name == "copy":
            msg = next(
                (m for m in reversed(self.store.messages(self.session_id)) if m.get("role") == "assistant"),
                None,
            )
            if msg is None:
                self.show_chat()
                now = datetime.now().strftime("%I:%M %p")
                self.write_message("assistant", "No assistant reply yet.", timestamp=now)
            else:
                self._copy_text(msg.get("content", ""))
        elif name == "history":
            self.show_chat()
            await asyncio.sleep(0)
            lines = "\n".join(
                f"- `{row.id}` {row.title}" for row in self.store.list_sessions()
            )
            now = datetime.now().strftime("%I:%M %p")
            self.write_message(
                "assistant",
                f"# Sessions\n{lines or 'No sessions yet.'}",
                timestamp=now,
            )
        elif name == "stats":
            self.show_chat()
            await asyncio.sleep(0)
            count = self.store.count_messages(self.session_id)
            now = datetime.now().strftime("%I:%M %p")
            self.write_message(
                "assistant",
                f"Session: {self.session_id}\nMessages: {count}\nModel: {self.settings.model}\nTheme: {self.active_theme.name}\nTokens generated: {self.total_tokens}",
                timestamp=now,
            )
        elif name == "config":
            self.show_chat()
            await asyncio.sleep(0)
            data = self.settings.as_dict()
            lines = "\n".join(f"  {key}: {value}" for key, value in data.items())
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", f"Config:\n{lines}", timestamp=now)
        elif name == "websearch":
            self.show_chat()
            sub = args.strip()
            sub_lower = sub.lower()
            now = datetime.now().strftime("%I:%M %p")
            if sub_lower in ("off", "false", "0"):
                self.websearch_enabled = False
                self.write_message("assistant", "Web search: **OFF**", timestamp=now)
            elif sub_lower in ("on", "true", "1"):
                self.websearch_enabled = True
                self.write_message("assistant", "Web search: **ON**", timestamp=now)
            elif sub_lower == "status":
                healthy, msg = await check_searxng_health(self.settings.searxng_url)
                status_icon = "🟢" if healthy else "🔴"
                msg_content = (
                    f"### 🔍 Web Search Status (SearXNG)\n\n"
                    f"- **Feature**: **{'ON' if self.websearch_enabled else 'OFF'}**\n"
                    f"- **Engine**: SearXNG Metasearch (Google, Bing, Brave, DDG, Reddit, etc.)\n"
                    f"- **Endpoint**: `{self.settings.searxng_url}`\n"
                    f"- **Connectivity**: {status_icon} {msg}\n"
                    f"- **Deep Reading (Jina Reader)**: {'Enabled' if self.settings.searxng_deep_read else 'Disabled'}\n"
                    f"- **Fallback Provider**: Wikipedia API + DuckDuckGo\n\n"
                    f"*To run your own local SearXNG with Docker:*\n"
                    f"```bash\n./scripts/run_searxng.sh\n```\n\n"
                    f"*To change endpoint:* `/websearch url http://your-searxng-host:8888`"
                )
                self.write_message("assistant", msg_content, timestamp=now)
            elif sub_lower.startswith("url"):
                new_url = sub[3:].strip()
                if new_url:
                    self.settings.save_searxng_url(new_url)
                    healthy, msg = await check_searxng_health(new_url)
                    status_icon = "🟢" if healthy else "🟡"
                    self.write_message(
                        "assistant",
                        f"SearXNG URL updated to `{new_url}`\nConnectivity: {status_icon} {msg}",
                        timestamp=now,
                    )
                else:
                    self.write_message(
                        "assistant",
                        f"Current SearXNG URL: `{self.settings.searxng_url}`\nUsage: `/websearch url <http://host:port>`",
                        timestamp=now,
                    )
            elif sub_lower.startswith("test"):
                query = sub[4:].strip() or "PyTorch latest version"
                self.write_message("assistant", f"✦ Running test search for: `{query}`...", timestamp=now)
                results = await web_search(query, top_n=3, searxng_url=self.settings.searxng_url)
                if results:
                    lines = [f"**Results for `{query}`:**\n"]
                    for idx, r in enumerate(results, 1):
                        engine = f" [{r['engine']}]" if r.get("engine") else ""
                        lines.append(f"{idx}. [{r.get('title', 'Link')}]({r.get('url', '')}){engine}")
                        if r.get("snippet"):
                            lines.append(f"   {r['snippet'][:200]}...")
                    self.write_message("assistant", "\n".join(lines), timestamp=now)
                else:
                    self.write_message("assistant", f"No results returned for `{query}`.", timestamp=now)
            else:
                self.websearch_enabled = not self.websearch_enabled
                self.write_message(
                    "assistant",
                    f"Web search: **{'ON' if self.websearch_enabled else 'OFF'}**\n\n"
                    f"Subcommands:\n"
                    f"- `/websearch on` / `/websearch off`\n"
                    f"- `/websearch status` - Check SearXNG connection\n"
                    f"- `/websearch url <url>` - Set custom SearXNG endpoint\n"
                    f"- `/websearch test <query>` - Run quick test search",
                    timestamp=now,
                )
        elif name == "mode":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            sub = args.strip().lower()
            if sub in ("agent", "code", "coder", "autonomous"):
                self.toggle_mode(True)
                self.write_message(
                    "assistant",
                    f"Mode switched to **🤖 AGENT (Autonomous Coding)**.\nWorkspace: `{self.settings.workspace_dir}`\nMax Steps: `{self.settings.agent_max_steps}`",
                    timestamp=now,
                )
            elif sub in ("chat", "conversation", "normal", "standard"):
                self.toggle_mode(False)
                self.write_message(
                    "assistant",
                    "Mode switched to **💬 CHAT (Standard Conversation)**.",
                    timestamp=now,
                )
            else:
                new_mode = self.toggle_mode()
                mode_name = "**🤖 AGENT (Autonomous Coding)**" if new_mode else "**💬 CHAT (Standard Conversation)**"
                self.write_message(
                    "assistant",
                    f"Mode toggled to: {mode_name}\n\n*Use `/mode chat` or `/mode agent` to switch explicitly.*",
                    timestamp=now,
                )
        elif name == "agent":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            sub = args.strip()
            sub_lower = sub.lower()
            if sub_lower in ("on", "1", "enable", "start"):
                self.toggle_mode(True)
                self.write_message(
                    "assistant",
                    f"Autonomous Coding Agent: **ON**\nWorkspace: `{self.settings.workspace_dir}`",
                    timestamp=now,
                )
            elif sub_lower in ("off", "0", "disable", "stop"):
                self.toggle_mode(False)
                self.write_message(
                    "assistant",
                    "Autonomous Coding Agent: **OFF** (Switched to Chat Mode)",
                    timestamp=now,
                )
            elif sub_lower == "status":
                from pathlib import Path
                ws = Path(self.settings.workspace_dir).resolve()
                exists = ws.exists()
                status_content = (
                    f"### 🤖 Autonomous Coding Agent Status\n\n"
                    f"- **Agent Mode**: **{'ACTIVE (🤖 AGENT)' if self.settings.agent_mode else 'INACTIVE (💬 CHAT)'}**\n"
                    f"- **Workspace Directory**: `{ws}` ({'exists' if exists else 'not found'})\n"
                    f"- **Max Autonomous Steps**: `{self.settings.agent_max_steps}`\n"
                    f"- **Tools Available**: `read_file`, `write_file`, `edit_file`, `list_dir`, `grep_search`, `run_command`\n"
                    f"- **Active Model**: `{self.settings.model}`\n\n"
                    f"*Subcommands:*\n"
                    f"- `/agent on` / `/agent off`\n"
                    f"- `/agent dir <path>` - Change active workspace path\n"
                    f"- `/agent steps <num>` - Change max autonomous steps limit"
                )
                self.write_message("assistant", status_content, timestamp=now)
            elif sub_lower.startswith("dir"):
                new_dir = sub[3:].strip()
                if new_dir:
                    from pathlib import Path
                    p = Path(new_dir).resolve()
                    if not p.exists():
                        try:
                            p.mkdir(parents=True, exist_ok=True)
                        except Exception as e:
                            self.write_message(
                                "assistant",
                                f"Failed to create workspace directory `{p}`: {e}",
                                timestamp=now,
                            )
                            return
                    self.settings.save_workspace_dir(str(p))
                    self.write_message(
                        "assistant",
                        f"Agent workspace directory updated to: `{p}`",
                        timestamp=now,
                    )
                else:
                    self.write_message(
                        "assistant",
                        f"Current agent workspace: `{self.settings.workspace_dir}`\nUsage: `/agent dir <path>`",
                        timestamp=now,
                    )
            elif sub_lower.startswith("steps"):
                val = sub[5:].strip()
                try:
                    n = int(val)
                    if n <= 0 or n > 50:
                        raise ValueError("Steps must be between 1 and 50")
                    self.settings.save_agent_max_steps(n)
                    self.write_message(
                        "assistant",
                        f"Agent max steps set to `{n}`",
                        timestamp=now,
                    )
                except ValueError:
                    self.write_message(
                        "assistant",
                        f"Invalid steps: `{val}`. Must be a number between 1 and 50.\nCurrent: `{self.settings.agent_max_steps}`",
                        timestamp=now,
                    )
            else:
                new_mode = self.toggle_mode()
                mode_str = "**ON**" if new_mode else "**OFF**"
                self.write_message(
                    "assistant",
                    f"Autonomous Coding Agent: {mode_str}\n\n"
                    f"Subcommands:\n"
                    f"- `/agent on` / `/agent off`\n"
                    f"- `/agent status`\n"
                    f"- `/agent dir <path>`\n"
                    f"- `/agent steps <num>`",
                    timestamp=now,
                )
        elif name in ("sidebar", "navbar", "nav"):
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            sub = args.strip().lower()
            if sub in ("hide", "off", "close", "0"):
                self.toggle_sidebar(show=False)
                self.write_message(
                    "assistant",
                    "Navbar is now **hidden** (Press `Ctrl+B` or click `☰` to show).",
                    timestamp=now,
                )
            elif sub in ("show", "on", "open", "1"):
                self.toggle_sidebar(show=True)
                self.write_message(
                    "assistant",
                    "Navbar is now **visible**.",
                    timestamp=now,
                )
            else:
                visible = self.toggle_sidebar()
                st = "**visible**" if visible else "**hidden**"
                self.write_message(
                    "assistant",
                    f"Navbar is now {st}. (Shortcut: `Ctrl+B`).",
                    timestamp=now,
                )
        elif name == "exit":
            self.exit()
        else:
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            self.write_message(
                "assistant",
                f"Unknown command: /{name}. Type /help.",
                timestamp=now,
            )

    # ── Status Bar ──────────────────────────────────────────────────────

    def update_status_bar(self) -> None:
        try:
            self.query_one("#topbar-model", Static).update(f"Model: {self.settings.model} ˅")
        except Exception:
            pass

        now = datetime.now().strftime("%I:%M:%S %p")
        try:
            self.query_one("#topbar-time", Static).update(now)
        except Exception:
            pass

        tok_s = f"{self.total_tokens / max(self.gen_time, 0.01):.1f}" if self.gen_time > 0 else "0.0"
        status_bar = (
            f"tok/s: {tok_s}  │  gen: {self.total_gen_count}  │  "
            f"time: {self.gen_time:.1f}s  │  temp: {self.settings.temperature}  │  "
            f"context: {self.context_used}/{self.context_max}  │  "
            f"theme: {self.active_theme.name}"
        )
        try:
            self.query_one("#statusbar", Static).update(status_bar)
        except Exception:
            pass

    # ── View Switching ──────────────────────────────────────────────────

    def show_landing(self) -> None:
        self._hide_autocomplete()
        self.query_one("#landing", Container).remove_class("hidden")
        self.query_one("#chat-area", Vertical).add_class("hidden")
        self.query_one("#prompt", Input).focus()

    def show_chat(self) -> None:
        self._hide_autocomplete()
        self.query_one("#landing", Container).add_class("hidden")
        self.query_one("#chat-area", Vertical).remove_class("hidden")
        self.query_one("#chat-prompt", ChatInput).focus()

    # ── Actions ─────────────────────────────────────────────────────────

    def action_command_palette(self) -> None:
        """Open the command palette modal."""
        def on_cmd(result: str | None) -> None:
            if result:
                cmd_base = result.split()[0]
                needs_args = "<" in result or "[" in result
                if cmd_base == "/theme":
                    self.action_theme_picker()
                    return
                self.show_chat()
                ta = self.query_one("#chat-prompt", ChatInput)
                if needs_args:
                    ta.text = f"{cmd_base} "
                    ta.cursor_location = (0, len(ta.text))
                    ta.focus()
                else:
                    ta.text = cmd_base
                    asyncio.create_task(self.action_submit_chat())
        self.push_screen(CommandPalette(), on_cmd)

    def action_close_autocomplete(self) -> None:
        self._hide_autocomplete()

    def _get_active_autocomplete_widgets(self) -> tuple[Vertical | None, ListView | None]:
        if not getattr(self, "_app_ready", False):
            return None, None
        is_landing = not self.query_one("#landing", Container).has_class("hidden")
        popup_id = "#landing-autocomplete" if is_landing else "#chat-autocomplete"
        list_id = "#landing-autocomplete-items" if is_landing else "#chat-autocomplete-items"
        try:
            popup = self.query_one(popup_id, Vertical)
            lst = self.query_one(list_id, ListView)
            return popup, lst
        except Exception:
            return None, None

    def _show_autocomplete(self, filter_text: str) -> None:
        popup, lst = self._get_active_autocomplete_widgets()
        if popup is None or lst is None:
            return
        matches = [
            (cmd, desc)
            for cmd, desc in self.commands
            if filter_text.lower() in cmd.lower()
        ]
        if matches:
            self._current_autocomplete_commands = [c[0] for c in matches]
            lst.clear()
            items = [AutoCompleteItem(cmd, desc) for cmd, desc in matches]
            lst.extend(items)
            lst.index = 0
            popup.add_class("visible")
            popup.styles.display = "block"
            self._autocomplete_visible = True
        else:
            self._hide_autocomplete()

    def _hide_autocomplete(self) -> None:
        for popup_id in ("#landing-autocomplete", "#chat-autocomplete"):
            try:
                p = self.query_one(popup_id, Vertical)
                p.remove_class("visible")
                p.styles.display = "none"
            except Exception:
                pass
        self._autocomplete_visible = False
        self._current_autocomplete_commands = []

    def _autocomplete_cursor_down(self) -> None:
        _, lst = self._get_active_autocomplete_widgets()
        if lst is not None and lst.children:
            lst.action_cursor_down()

    def _autocomplete_cursor_up(self) -> None:
        _, lst = self._get_active_autocomplete_widgets()
        if lst is not None and lst.children:
            lst.action_cursor_up()

    def _accept_autocomplete(
        self,
        submit_if_no_args: bool = False,
        selected_cmd: str | None = None,
    ) -> None:
        cmd: str | None = selected_cmd
        if not cmd:
            _, lst = self._get_active_autocomplete_widgets()
            if lst is not None and lst.children:
                item = lst.highlighted_child or lst.children[0]
                if isinstance(item, AutoCompleteItem):
                    cmd = item.command
            if not cmd and getattr(self, "_current_autocomplete_commands", None):
                idx = 0
                if lst is not None and lst.index is not None and 0 <= lst.index < len(self._current_autocomplete_commands):
                    idx = lst.index
                cmd = self._current_autocomplete_commands[idx]
        if not cmd:
            self._hide_autocomplete()
            return

        self._hide_autocomplete()
        self._suppress_autocomplete = True

        cmd_base = cmd.split()[0]
        if cmd_base == "/theme":
            self.action_theme_picker()
            return

        needs_args = "<" in cmd or "[" in cmd
        is_landing = not self.query_one("#landing", Container).has_class("hidden")
        if is_landing:
            inp = self.query_one("#prompt", LandingInput)
            if needs_args:
                inp.value = f"{cmd_base} "
                inp.cursor_position = len(inp.value)
                inp.focus()
            else:
                inp.value = cmd_base
                if submit_if_no_args:
                    asyncio.create_task(self._process_input(cmd_base))
        else:
            ta = self.query_one("#chat-prompt", ChatInput)
            if needs_args:
                ta.text = f"{cmd_base} "
                ta.cursor_location = (0, len(ta.text))
                ta.focus()
            else:
                ta.text = cmd_base
                if submit_if_no_args:
                    asyncio.create_task(self.action_submit_chat())

    def on_key(self, event) -> None:
        if self._autocomplete_visible and event.key == "escape":
            event.stop()
            event.prevent_default()
            self._hide_autocomplete()
            return

    def action_new_session(self) -> None:
        import uuid
        self.session_id = uuid.uuid4().hex[:12]
        self.refresh_sessions()
        self.load_current_session()
        self.total_tokens = 0
        self.total_gen_count = 0
        self.gen_time = 0.0
        self.context_used = 0
        self.update_status_bar()

    def action_clear_chat(self) -> None:
        self.query_one("#chat-messages", Vertical).remove_children()
        self.show_landing()

    def action_cancel_stream(self) -> None:
        if self.streaming_task and not self.streaming_task.done():
            self.streaming_task.cancel()

    def action_delete_session(self) -> None:
        """Delete the highlighted or current session."""
        sessions_view = self.query_one("#sessions", ListView)
        if sessions_view.has_focus and sessions_view.index is not None and sessions_view.index < len(self._session_ids):
            target_id = self._session_ids[sessions_view.index]
        else:
            target_id = self.session_id

        self.store.delete_session(target_id)
        remaining = self.store.list_sessions()
        if target_id == self.session_id:
            if remaining:
                self.session_id = remaining[0].id
                self.load_current_session()
            else:
                import uuid
                self.session_id = uuid.uuid4().hex[:12]
                self.show_landing()
        self.refresh_sessions()
        self.update_status_bar()
        self.notify("Session deleted")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id in ("landing-autocomplete-items", "chat-autocomplete-items"):
            selected_cmd = event.item.command if isinstance(event.item, AutoCompleteItem) else None
            self._accept_autocomplete(submit_if_no_args=True, selected_cmd=selected_cmd)
        elif event.list_view.id == "sessions":
            idx = event.list_view.index
            if idx is not None and idx < len(self._session_ids):
                self.session_id = self._session_ids[idx]
                self.refresh_sessions()
                self.load_current_session()
                self.update_status_bar()

    def on_click(self, event: events.Click) -> None:
        """Handle clicks on sidebar nav, suggestions, top bar, send button, footer commands, and copy buttons."""
        target = event.widget
        if target is None:
            return

        node = target
        while node is not None and node is not self:
            wid = getattr(node, "id", None)
            classes = getattr(node, "classes", ())
            if wid == "nav-new" or wid == "cmd-new" or "cmd-new" in classes:
                self.action_new_session()
                return
            if wid == "nav-chats":
                if self.store.count_messages(self.session_id) > 0:
                    self.show_chat()
                else:
                    sessions = self.store.list_sessions()
                    if sessions:
                        self.session_id = sessions[0].id
                        self.refresh_sessions()
                        self.load_current_session()
                self.query_one("#sessions", ListView).focus()
                return
            if wid == "nav-settings":
                asyncio.create_task(self.run_command("config", ""))
                return
            if wid == "nav-toggle" or "nav-toggle-btn" in classes:
                self.toggle_sidebar()
                return
            if wid == "topbar-mode" or "topbar-mode" in classes or "topbar-mode-agent" in classes or "topbar-mode-chat" in classes:
                self.toggle_mode()
                return
            if wid == "cmd-mode" or "cmd-mode" in classes:
                self.toggle_mode()
                return
            if wid == "cmd-agent" or "cmd-agent" in classes:
                self._populate_command_input("/agent ")
                return
            if wid in ("topbar-help", "cmd-help") or "cmd-help" in classes:
                self.action_command_palette()
                return
            if wid in ("topbar-model", "cmd-model") or "cmd-model" in classes:
                self._populate_command_input("/model ")
                return
            if wid == "cmd-theme" or "cmd-theme" in classes:
                self.action_theme_picker()
                return
            if wid == "cmd-rename" or "cmd-rename" in classes:
                self._populate_command_input("/rename ")
                return
            if wid == "cmd-delete" or "cmd-delete" in classes:
                self.action_delete_session()
                return
            if wid == "cmd-clear" or "cmd-clear" in classes:
                self.action_clear_chat()
                return
            if wid == "sug-1":
                self._apply_suggestion("Explain the concept of ")
                return
            if wid == "sug-2":
                self._apply_suggestion("Write a Python function to ")
                return
            if wid == "sug-3":
                self._apply_suggestion("Help me plan a project for ")
                return
            if wid in ("send-btn", "landing-send-btn"):
                asyncio.create_task(self.action_submit_chat())
                return
            if "code-copy" in classes:
                cb = node
                while cb is not None and not isinstance(cb, CodeBlock):
                    cb = cb.parent
                if isinstance(cb, CodeBlock):
                    self._copy_text(cb.code)
                    return
            if "msg-copy" in classes:
                cm = node
                while cm is not None and not isinstance(cm, ChatMessage):
                    cm = cm.parent
                if isinstance(cm, ChatMessage):
                    self._copy_text(cm.msg_content)
                    return
            node = node.parent

    def _copy_text(self, text: str) -> None:
        """Copy text via OSC 52, with wl-copy and xclip fallbacks."""
        if not text:
            self.notify("Nothing to copy", severity="warning")
            return
        self.copy_to_clipboard(text)
        try:
            subprocess.run(
                ["wl-copy"],
                input=text.encode("utf-8"),
                check=False,
                capture_output=True,
                timeout=1,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"),
                    check=False,
                    capture_output=True,
                    timeout=1,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        self.notify("Copied to clipboard")

    def action_copy_last_reply(self) -> None:
        """Copy the last assistant reply to the clipboard."""
        for msg in reversed(self.store.messages(self.session_id)):
            if msg.get("role") == "assistant":
                self._copy_text(msg.get("content", ""))
                return
        self.notify("No assistant reply yet", severity="warning")
