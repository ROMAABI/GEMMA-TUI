from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path

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
from textual.widgets import Button, Input, Label, ListItem, ListView, Static, TextArea

from app.lifecycle import async_server_is_healthy, async_get_loaded_model
from config.settings import Settings
from core.agent import AgentOrchestrator, AgentEvent
from core.client import LlamaClient
from core.commands import COMMAND_DEFS, HELP_TEXT, parse_command
from core.compaction import Compactor
from core.lsp import LSPManager, lsp_tools
from core.mcp import MCPManager
from core.orchestrator import decide_search
from core.permissions import (
    PermissionDenied,
    PermissionManager,
    PermissionRequest,
    PermissionRule,
    default_rules,
    parse_rule,
)
from core.research import search_context, web_search, check_searxng_health
from core.skills import SkillsManager
from core.titles import suggest_title
from core.tooling import build_default_registry
from core.tools import WorkspaceTools, AGENT_SYSTEM_PROMPT
from sessions.store import SessionStore, format_session_timestamp
from tui.themes import THEMES, DEFAULT_THEME, Theme, get_theme, generate_all_themes_css


def format_incomplete_agent_message(failures: list[tuple[str, str]]) -> str:
    """Honest fallback when the agent stops without producing final text."""
    if not failures:
        return (
            "The agent stopped without a final answer. Review the tool output "
            "above; the task may not be complete."
        )
    lines = ["The agent stopped without a final answer. Some tool calls failed:"]
    for name, output in failures[-3:]:
        detail = " ".join(str(output or "").split())
        if len(detail) > 220:
            detail = detail[:217] + "..."
        lines.append(f"- `{name}`: {detail}")
    lines.append("The task is not confirmed complete.")
    return "\n".join(lines)


def _load_unfiltered_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "UNFILTERED_SYSTEM_PROMPT.md")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        if "---" not in lines:
            return "\n".join(lines).strip()
        return "\n".join(lines[lines.index("---") + 1 :]).strip()
    except OSError:
        return (
            "You are Gemma, a friendly assistant running inside the gemma app on the "
            "user's own computer. In this chat mode you answer directly, without tools; "
            "agent mode in the same app can run commands on this machine. Never claim "
            "the user's computer is out of your reach. Reply directly without thinking "
            "or reasoning steps."
        )


SYSTEM_PROMPT = _load_unfiltered_prompt()

# Greetings never need a web-search decision. Skipping the judge call saves a
# full extra model round-trip for "hi" / "thanks" / etc.
_SMALLTALK_RE = re.compile(
    r"^(hi|hey|hello|yo|sup|hiya)(\s+(gemma|there|bot|assistant|friend))?[\s!.,?]*$"
    r"|^(thanks|thank you|thx|ok|okay|"
    r"good (morning|afternoon|evening|night)|how are you|how's it going|"
    r"what'?s up|bye|goodbye)[\s!.,?]*$",
    re.IGNORECASE,
)


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
        ttft: float | None = None,
        timestamp: str | None = None,
        syntax_theme: str = "monokai",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.role = role
        self.msg_content = content
        self.elapsed = elapsed
        self.ttft = ttft
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
                    label = f"⚡ {self.elapsed:.1f}s"
                    if self.ttft is not None and self.ttft >= 1.0:
                        label += f" · first token {self.ttft:.1f}s"
                    yield Static(label, classes="elapsed-badge")


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
        name = self.tool_name
        if name in ("read", "read_file"):
            p = self.args.get("path", "")
            s = self.args.get("start_line") or self.args.get("start")
            e = self.args.get("end_line") or self.args.get("end")
            if s and e:
                return f"{p}:{s}-{e}"
            return p
        if name in ("write", "write_file", "edit", "edit_file"):
            return str(self.args.get("path", ""))
        if name in ("bash", "run_command", "shell"):
            return str(self.args.get("command", ""))[:55]
        if name in ("grep", "grep_search"):
            q = self.args.get("pattern") or self.args.get("query", "")
            include = self.args.get("include")
            return f"'{q}'" + (f" in {include}" if include else "")
        if name in ("glob", "glob_search"):
            return str(self.args.get("pattern", ""))
        if name in ("list_dir", "list", "ls"):
            return str(self.args.get("path", "."))
        if name in ("patch", "apply_patch"):
            text = str(self.args.get("patch_text", ""))
            files = re.findall(r"\*\*\* (?:Add|Update|Delete) File:\s*(.+)", text)
            return ", ".join(files[:4]) if files else "multi-file patch"
        if name.startswith("git_"):
            return str(self.args.get("message") or self.args.get("path") or name)
        if name.startswith("lsp_"):
            return f"{self.args.get('path', '')}:{self.args.get('line', '')}"
        if name == "skill":
            return str(self.args.get("name", ""))
        if name.startswith("mcp__"):
            return name
        return str(self.args)[:50]

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
        kwargs.setdefault("placeholder", "Ask Gemma anything… (/ for commands)")
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
    #theme-list > ListItem.-highlight {
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


class ModelItem(ListItem):
    """ListItem displaying a model in the ModelPicker dialog."""

    def __init__(self, name: str, active: bool = False, source: str = "") -> None:
        self.model_name = name
        marker = "● " if active else "  "
        super().__init__(
            Horizontal(
                Static(f" {marker}{name}", classes="model-name"),
                Static(source, classes="model-source"),
            )
        )


class ModelPicker(ModalScreen[str | None]):
    """Modal overlay to browse and select the active model."""

    DEFAULT_CSS = """
    ModelPicker {
        align: center top;
        background: rgba(10, 10, 10, 0.85);
    }
    #model-dialog {
        width: 1fr;
        max-width: 68;
        height: auto;
        margin-top: 5;
        background: #181818;
        border: round #22d3ee;
        padding: 1 2;
    }
    #model-title {
        text-style: bold;
        color: #22d3ee;
        height: 1;
    }
    #model-sub {
        color: #6b7280;
        height: 1;
        margin-bottom: 1;
    }
    #model-list {
        height: auto;
        max-height: 14;
        background: transparent;
        border: none;
        padding: 0;
    }
    #model-list > ListItem {
        height: 2;
        padding: 0 1;
        color: #e5e7eb;
    }
    #model-list > ListItem:hover,
    #model-list > ListItem.-highlight {
        background: #232323;
        color: #22d3ee;
    }
    .model-name {
        width: 1fr;
        text-style: bold;
    }
    .model-source {
        width: auto;
        color: #6b7280;
    }
    #model-hint {
        height: 1;
        margin-top: 1;
        color: #6b7280;
        content-align: center middle;
    }
    """

    def __init__(self, current_model: str, models: list[tuple[str, str]]) -> None:
        super().__init__()
        self.current_model = current_model
        self.models = models

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static("Select Model", id="model-title")
            yield Static("Click a model to make it active", id="model-sub")
            yield ListView(id="model-list")
            yield Static("↑↓ move · Enter select · Esc cancel", id="model-hint")

    def on_mount(self) -> None:
        lst = self.query_one("#model-list", ListView)
        lst.clear()
        active_idx = 0
        for i, (name, source) in enumerate(self.models):
            active = name == self.current_model
            if active:
                active_idx = i
            lst.append(ModelItem(name, active=active, source=source))
        if lst.children:
            lst.index = active_idx
        lst.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ModelItem):
            self.dismiss(event.item.model_name)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "down":
            event.stop()
            self.query_one("#model-list", ListView).action_cursor_down()
        elif event.key == "up":
            event.stop()
            self.query_one("#model-list", ListView).action_cursor_up()


class SettingsItem(ListItem):
    """A row in the SettingsMenu dialog."""

    def __init__(self, key: str, label: str, value: str = "") -> None:
        self.key = key
        super().__init__(
            Horizontal(
                Static(f" {label}", classes="settings-label"),
                Static(value, classes="settings-value"),
            )
        )


class SettingsMenu(ModalScreen[str | None]):
    """Small clickable settings menu: model, theme, modes, config."""

    DEFAULT_CSS = """
    SettingsMenu {
        align: center top;
        background: rgba(10, 10, 10, 0.85);
    }
    #settings-dialog {
        width: 1fr;
        max-width: 56;
        height: auto;
        margin-top: 5;
        background: #181818;
        border: round #22d3ee;
        padding: 1 2;
    }
    #settings-title {
        text-style: bold;
        color: #22d3ee;
        height: 1;
        margin-bottom: 1;
    }
    #settings-list {
        height: auto;
        max-height: 12;
        background: transparent;
        border: none;
        padding: 0;
    }
    #settings-list > ListItem {
        height: 2;
        padding: 0 1;
        color: #e5e7eb;
    }
    #settings-list > ListItem:hover,
    #settings-list > ListItem.-highlight {
        background: #232323;
        color: #22d3ee;
    }
    .settings-label {
        width: 1fr;
        text-style: bold;
    }
    .settings-value {
        width: auto;
        color: #9ca3af;
    }
    #settings-hint {
        height: 1;
        margin-top: 1;
        color: #6b7280;
        content-align: center middle;
    }
    """

    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("Settings", id="settings-title")
            yield ListView(id="settings-list")
            yield Static("↑↓ move · Enter select · Esc close", id="settings-hint")

    def on_mount(self) -> None:
        lst = self.query_one("#settings-list", ListView)
        lst.clear()
        for key, label, value in self.rows:
            lst.append(SettingsItem(key, label, value))
        if lst.children:
            lst.index = 0
        lst.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SettingsItem):
            self.dismiss(event.item.key)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "down":
            event.stop()
            self.query_one("#settings-list", ListView).action_cursor_down()
        elif event.key == "up":
            event.stop()
            self.query_one("#settings-list", ListView).action_cursor_up()


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
    #command-list > ListItem.-highlight {
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


class PermissionScreen(ModalScreen[str]):
    """Modal approval dialog for a tool call that needs permission."""

    DEFAULT_CSS = """
    PermissionScreen {
        align: center middle;
    }

    #permission-dialog {
        width: 86;
        max-width: 94%;
        height: auto;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }

    #permission-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #permission-summary {
        text-style: bold;
        margin-bottom: 1;
    }

    #permission-reason {
        color: $text-muted;
        margin-bottom: 1;
    }

    #permission-args {
        background: $panel;
        padding: 1;
        margin-bottom: 1;
        max-height: 12;
        overflow-y: auto;
    }

    #permission-buttons {
        height: auto;
        align-horizontal: right;
    }

    #permission-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [("escape", "deny", "Deny")]

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="permission-dialog"):
            yield Static("⚠  Permission required", id="permission-title")
            yield Static(self.request.summary(), id="permission-summary")
            if self.request.reason:
                yield Static(self.request.reason, id="permission-reason")
            try:
                args = json.dumps(self.request.args, indent=2, ensure_ascii=False)
            except Exception:
                args = str(self.request.args)
            if len(args) > 1800:
                args = args[:1800] + "\n... [truncated]"
            yield Static(args, id="permission-args")
            with Horizontal(id="permission-buttons"):
                yield Button("Allow once", id="allow", variant="primary")
                yield Button("Always allow", id="always")
                yield Button("Deny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")

    def action_deny(self) -> None:
        self.dismiss("deny")


# ── Main TUI Application ───────────────────────────────────────────────

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
    CSS_PATH = "gemma_tui.css"
    CSS = generate_all_themes_css()

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
        ("ctrl+w", "web_ui", "Web"),
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
        self.last_gen_tokens = 0
        self.gen_time_active = 0.0
        self._autocomplete_visible = False
        self._autocomplete_index = 0
        self._pending_mounts: list[tuple[Vertical, Widget]] = []
        self._pending_title: str | None = None
        self.websearch_enabled = True
        self.active_theme = get_theme(self.settings.theme)
        self._screen_cols: int = _detect_screen_columns()
        self._sidebar_visible: bool = True
        self.web_server = None
        self._last_below_60: bool | None = None
        self.context_max = max(2_000, int(getattr(settings, "context_limit", 32_768)))
        self.last_provider = getattr(settings, "provider", "auto")
        self._integrations_lock = asyncio.Lock()
        self._warmup_task: asyncio.Task[bool] | None = None
        self._warmup_ready = False
        self._warmup_failed = False
        self._mcp_started = False
        self._mcp_error = ""

        permission_rules: list[PermissionRule] = []
        for raw in getattr(settings, "permission_rules", []) or []:
            try:
                permission_rules.append(parse_rule(raw))
            except ValueError:
                continue
        self.permission_manager = PermissionManager(
            mode=getattr(settings, "permission_mode", "auto"),
            rules=permission_rules,
            approval=self._ask_permission,
            session_id=self.session_id,
        )
        self.skills: SkillsManager | None = None
        if getattr(settings, "skills_enabled", True):
            self.skills = SkillsManager(
                settings.workspace_dir,
                getattr(settings, "skills_dirs", []),
            )
        self.mcp: MCPManager | None = None
        if getattr(settings, "mcp_enabled", True):
            self.mcp = MCPManager(settings.workspace_dir, getattr(settings, "mcp_config", ".mcp.json"))
        self.lsp: LSPManager | None = None
        if getattr(settings, "lsp_enabled", True):
            self.lsp = LSPManager(settings.workspace_dir)

    def compose(self) -> ComposeResult:
        with Horizontal(id="shell"):
            # ── Left panel: navigation ──
            with Vertical(id="sidebar"):
                with Vertical(id="brand-card"):
                    with Vertical(id="sidebar-header"):
                        yield Static("◆  GEMMA TUI", id="brand")
                        yield Static("Local AI · offline", id="brand-sub")
                with Vertical(id="nav-card"):
                    yield Static("NAVIGATION", id="nav-title", classes="nav-section-title")
                    yield Static("+  New Chat", id="nav-new")
                    yield Static("💬  Chats", id="nav-chats")
                with Vertical(id="recents-card"):
                    yield Static("RECENT CHATS", id="recents-title", classes="nav-section-title")
                    yield ListView(id="sessions")
                with Vertical(id="settings-card"):
                    yield Static("⚙  Settings", id="nav-settings")

            # ── Workspace ──
            with Vertical(id="workspace"):
                # Top bar
                with Horizontal(id="topbar"):
                    with Horizontal(id="topbar-left"):
                        yield Static("☰", id="nav-toggle", classes="nav-toggle-btn")
                        yield Static(f"Model: {self._model_label()} ˅", id="topbar-model")
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
                                placeholder="Ask Gemma anything… (/ for commands)",
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
                            yield Static("↑↓ • Enter • Ctrl+L • Ctrl+Q", id="footer-hints", classes="footer-hints")

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
                            yield Static("↑↓ • Enter • Ctrl+L • Ctrl+Q", classes="footer-hints")

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
        # Layout settles after the resize event; refit the header text then.
        self.call_after_refresh(self._update_model_label)

    def _apply_responsive_layout(self, width: int, height: int) -> None:
        if width <= 0:
            import shutil
            width = shutil.get_terminal_size((160, 40)).columns

        if width > self._screen_cols:
            self._screen_cols = width

        threshold = self._screen_cols * 0.6
        is_below_60 = width < threshold or width < 104

        was_below_60 = self._last_below_60
        if was_below_60 != is_below_60:
            self._last_below_60 = is_below_60
            if is_below_60:
                self.toggle_sidebar(show=False, notify=False)
            else:
                self.toggle_sidebar(show=True, notify=False)

        try:
            if is_below_60 or width < 112:
                self.screen.add_class("responsive-compact")
            else:
                self.screen.remove_class("responsive-compact")
        except Exception:
            pass

        self._apply_footer_layout(width)
        self.update_status_bar()

    # Widths of the footer pieces, in terminal cells. These mirror the CSS
    # above: every command is rendered with a one-cell right margin.
    _FOOTER_ALL_COMMANDS = 54  # /new /mode /model /theme /rename /delete /clear /help
    _FOOTER_COMPACT_COMMANDS = 31  # without /rename /delete /clear
    _FOOTER_MINI_COMMANDS = 24  # without /rename /delete /clear /theme
    _FOOTER_HINTS = 28  # "↑↓ • Enter • Ctrl+L • Ctrl+Q"

    def _footer_available_width(self, width: int) -> int:
        """Best estimate of the visible footer's inner width at this size."""
        sidebar = 0
        if getattr(self, "_sidebar_visible", True):
            try:
                sidebar = self.query_one("#sidebar", Vertical).outer_size.width
            except Exception:
                sidebar = 38
        try:
            landing_visible = not self.query_one("#landing", Container).has_class("hidden")
        except Exception:
            landing_visible = True
        if landing_visible:
            # #landing-inner is capped at 84 and padded by one cell per side.
            return max(0, min(82, width - sidebar - 2))
        # #input-area pads three cells per side.
        return max(0, width - sidebar - 6)

    def _apply_footer_layout(self, width: int) -> None:
        """Hide footer items that would otherwise overlap at this width."""
        if width <= 0:
            return
        footer = self._footer_available_width(width)
        try:
            screen = self.screen
            for name, hidden in (
                ("footer-tight", footer < self._FOOTER_ALL_COMMANDS + self._FOOTER_HINTS),
                ("footer-compact", footer < self._FOOTER_ALL_COMMANDS),
                ("footer-mini", footer < self._FOOTER_COMPACT_COMMANDS),
                ("footer-nano", footer < self._FOOTER_MINI_COMMANDS),
            ):
                if hidden:
                    screen.add_class(name)
                else:
                    screen.remove_class(name)
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
            self._apply_footer_layout(self.size.width)
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
            ph = "Ask Gemma to code, edit files… (/ for commands)" if self.settings.agent_mode else "Ask Gemma anything… (/ for commands)"
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
        if self.settings.agent_mode:
            # Prime the agent prefix the moment the user switches to agent mode.
            self._start_warmup()
        else:
            # Chat mode does not use the agent prefix; stop a warmup that is
            # still hogging the CPU so the next message is not stuck behind it.
            task = self._warmup_task
            if task is not None and not task.done():
                task.cancel()
                self._warmup_task = None
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
                self._start_warmup()
            else:
                status_widget.update("○  DISCONNECTED")
                status_widget.styles.color = "#EF4444"
            self.update_status_bar()
        except Exception:
            pass

    # ── Prompt cache warmup ─────────────────────────────────────────────

    def _start_warmup(self) -> None:
        """Prime the model prompt cache in the background (best effort)."""
        if not getattr(self.settings, "agent_warmup", True):
            return
        if not self.settings.agent_mode:
            # Chat mode uses a tiny system prompt, so there is nothing worth
            # caching. Priming the agent prefix here only competes with the
            # user's first message on CPU-only servers (it cost 60-110s).
            return
        if self._warmup_ready or self._warmup_failed:
            return
        if getattr(self, "is_headless", False):
            return
        task = self._warmup_task
        if task is not None and not task.done():
            return
        self._warmup_task = asyncio.create_task(self._warmup_prompt_cache())

    async def _warmup_prompt_cache(self) -> bool:
        try:
            registry = await self._build_agent_registry()
            orchestrator = AgentOrchestrator(
                client=self.client,
                tools=WorkspaceTools(root_dir=self.settings.workspace_dir),
                settings=self.settings,
                max_steps=1,
                registry=registry,
                skills=self.skills,
            )
            ok = await orchestrator.warmup()
        except Exception:  # noqa: BLE001 - warmup must never break the app
            ok = False
        self._warmup_ready = ok
        self._warmup_failed = not ok
        return ok

    async def _await_warmup(self) -> None:
        """Wait for an in-flight warmup so the real request does not compete.

        llama.cpp processes requests in slots; running the warmup and the real
        prompt at the same time would make both slower. The warmup primes the
        exact prefix the real request needs, so waiting is not wasted work.
        """
        task = self._warmup_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=900)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001
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
        self.update_status_bar()

    # ── Web UI ──────────────────────────────────────────────────────────

    def ensure_web_server(self):
        """Start the local web UI server, sharing this TUI's session store."""
        server = getattr(self, "web_server", None)
        if server is not None and server.running:
            return server

        from core.webui import GemmaWebServer

        port = 0 if getattr(self, "is_headless", False) else 47317
        try:
            server = GemmaWebServer(self.settings, store=self.store, port=port)
            server.start()
        except Exception as exc:  # noqa: BLE001 - the TUI must keep working
            self.notify(f"Web UI failed to start: {exc}", severity="error")
            self.web_server = None
            return None
        self.web_server = server
        return server

    async def action_web_ui(self) -> None:
        await self.run_command("web", "")

    def on_unmount(self) -> None:
        server = getattr(self, "web_server", None)
        if server is not None:
            server.stop()
            self.web_server = None

    def action_theme_picker(self) -> None:
        def on_theme_selected(theme_id: str | None) -> None:
            if theme_id:
                self.switch_theme(theme_id)
        self.push_screen(ThemePicker(self.active_theme.id), on_theme_selected)

    # ── Model Management ────────────────────────────────────────────────

    def action_model_picker(self) -> None:
        asyncio.create_task(self.open_model_picker())

    async def _collect_models(self) -> list[tuple[str, str]]:
        """Current, server-loaded and local GGUF models, de-duplicated."""
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(name: str, source: str) -> None:
            name = (name or "").strip()
            if name and name not in seen:
                seen.add(name)
                entries.append((name, source))

        add(self.settings.model, "current")
        try:
            loaded = await async_get_loaded_model(self.settings.base_url)
            if loaded:
                add(loaded, "loaded")
        except Exception:
            pass
        try:
            models_dir = Path(__file__).parent.parent / "models"
            for path in sorted(models_dir.glob("*.gguf")):
                add(path.name, "local")
        except Exception:
            pass
        return entries

    async def open_model_picker(self) -> None:
        models = await self._collect_models()

        def on_model_selected(model: str | None) -> None:
            if not model or model == self.settings.model:
                return
            self.settings.save_model(model)
            self.update_status_bar()
            self.notify(f"Model set to {model}", timeout=2)

        self.push_screen(ModelPicker(self.settings.model, models), on_model_selected)

    # ── Settings Menu ───────────────────────────────────────────────────

    def action_settings_menu(self) -> None:
        rows = [
            ("model", "Model", self._model_label(26)),
            ("theme", "Theme", self.active_theme.name),
            ("mode", "Mode", "Agent" if self.settings.agent_mode else "Chat"),
            ("permissions", "Permissions", self.settings.permission_mode),
            ("config", "Show config", ""),
        ]

        def on_selected(key: str | None) -> None:
            if key == "model":
                self.action_model_picker()
            elif key == "theme":
                self.action_theme_picker()
            elif key == "mode":
                self.toggle_mode()
            elif key == "permissions":
                self._populate_command_input("/permissions mode ")
            elif key == "config":
                asyncio.create_task(self.run_command("config", ""))

        self.push_screen(SettingsMenu(rows), on_selected)

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
        tool_results: dict[str, str] = {}
        for message in messages:
            if message.get("role") == "tool":
                call_id = message.get("tool_call_id") or ""
                if call_id:
                    tool_results[call_id] = message.get("content", "")

        step = 0
        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "")
            ts_str = None
            if message.get("created_at"):
                try:
                    dt = datetime.fromisoformat(message["created_at"])
                    ts_str = dt.strftime("%I:%M %p")
                except Exception:
                    ts_str = message["created_at"]

            if role == "system":
                continue
            if role == "tool":
                continue

            metadata = message.get("metadata") or {}
            if role == "assistant" and metadata.get("tool_calls"):
                for call in metadata["tool_calls"]:
                    step += 1
                    function = call.get("function") or {}
                    name = function.get("name", "tool")
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    result = tool_results.get(call.get("id", ""), "")
                    status = "error" if result.startswith("Error") else "done"
                    card = ToolStepCard(
                        tool_name=name,
                        args=args if isinstance(args, dict) else {},
                        step=step,
                        status=status,
                        result=result,
                    )
                    self._mount_or_queue(container, card)
                if content.strip():
                    self.write_message("assistant", content, timestamp=ts_str)
                continue

            if role in ("user", "assistant") and content.strip():
                self.write_message(role, content, timestamp=ts_str)

        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    # ── Message Display ─────────────────────────────────────────────────

    def write_message(
        self,
        role: str,
        content: str,
        elapsed: float | None = None,
        timestamp: str | None = None,
        ttft: float | None = None,
    ) -> None:
        container = self.query_one("#chat-messages", Vertical)
        msg = ChatMessage(
            role,
            content,
            elapsed=elapsed if role == "assistant" else None,
            ttft=ttft if role == "assistant" else None,
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

            if self.websearch_enabled and not _SMALLTALK_RE.match(latest_user_text.strip()):
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

            tokens_before = self.total_tokens
            first_token_at: float | None = None
            async for chunk in self.client.stream_chat(messages):
                if first_token_at is None and chunk:
                    first_token_at = time.monotonic()
                self.output_buffer += chunk
                self.total_tokens += 1
                indicator.update(Text(f"✦ {self.output_buffer}"))
                scroll.scroll_end(animate=False)

            elapsed = max(time.monotonic() - started, 0.001)
            ttft = (first_token_at - started) if first_token_at else elapsed
            self.gen_time = elapsed
            self.last_gen_tokens = self.total_tokens - tokens_before
            self.gen_time_active = max(elapsed - ttft, 0.001)
            self.token_count = len(self.output_buffer.split())
            self.context_used = min(
                self.context_used + self.token_count, self.context_max
            )

            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message(
                "assistant", self.output_buffer, elapsed=elapsed, ttft=ttft, timestamp=now
            )
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

    async def _ask_permission(self, request: PermissionRequest) -> str:
        """Show a modal and await the user's decision."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def done(result: str | None) -> None:
            if not future.done():
                future.set_result(result or "deny")

        try:
            self.push_screen(PermissionScreen(request), done)
        except Exception:
            return "deny"
        try:
            return await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            return "deny"

    async def _build_agent_registry(self):
        registry = build_default_registry()
        if self.skills is not None:
            try:
                self.skills.reload()
                for spec in self.skills.tool_specs():
                    if registry.get(spec.name) is None:
                        registry.register(spec)
            except Exception:
                pass
        if self.lsp is not None:
            try:
                for spec in lsp_tools(self.lsp):
                    if registry.get(spec.name) is None:
                        registry.register(spec)
            except Exception:
                pass
        if self.mcp is not None:
            async with self._integrations_lock:
                if not self._mcp_started:
                    try:
                        await self.mcp.start()
                    except Exception as exc:  # noqa: BLE001
                        self._mcp_error = str(exc)
                    finally:
                        # Avoid a slow 20s retry on every message; /mcp reload
                        # clears this when the user changes the config.
                        self._mcp_started = True
            try:
                self.mcp.register_tools(registry)
            except Exception:
                pass
        return registry

    async def _stream_agent_reply(
        self,
        container: Vertical,
        indicator: Static,
        scroll: VerticalScroll,
        started: float,
    ) -> None:
        indicator.update("✦ Initializing Autonomous Coding Agent...")
        scroll.scroll_end(animate=False)

        try:
            registry = await self._build_agent_registry()
        except Exception as exc:  # noqa: BLE001
            registry = build_default_registry()
            self.notify(f"Integration setup warning: {exc}", severity="warning")

        tools = WorkspaceTools(root_dir=self.settings.workspace_dir)
        self.permission_manager.session_id = self.session_id
        self.permission_manager.approval = self._ask_permission
        orchestrator = AgentOrchestrator(
            client=self.client,
            tools=tools,
            settings=self.settings,
            max_steps=self.settings.agent_max_steps,
            registry=registry,
            permission=self.permission_manager,
            approval=self._ask_permission,
            store=self.store,
            session_id=self.session_id,
            skills=self.skills,
        )

        messages = self.store.context_messages(self.session_id)
        active_cards: dict[str, ToolStepCard] = {}
        current_thought = ""
        current_reasoning = ""
        final_answer = ""
        last_usage: dict[str, int] = {}
        failed_tools: list[tuple[str, str]] = []
        current_step = 0
        max_steps = getattr(self.settings, "agent_max_steps", 10)
        phase = "initializing"
        active_tool = ""

        def _tail(text: str, limit: int = 110) -> str:
            text = " ".join(text.split())
            return text if len(text) <= limit else "..." + text[-limit:]

        def _render_status() -> None:
            elapsed = time.monotonic() - started
            icon = {
                "reasoning": "🧠",
                "writing": "🤖",
                "tool": "⏳",
            }.get(phase, "✦")
            if phase == "initializing":
                body = "initializing agent..."
            elif phase == "warming_up":
                body = "warming up model (the first request is the slowest)..."
            elif phase == "waiting":
                body = "waiting for model..."
            elif phase == "reasoning":
                body = _tail(current_reasoning) or "thinking..."
            elif phase == "writing":
                body = _tail(current_thought) or "writing..."
            elif phase == "tool":
                body = f"running {active_tool}..." if active_tool else "running tool..."
            elif phase == "analyzing":
                body = "analyzing result..."
            else:
                body = ""
            step_text = f"Step {current_step}/{max_steps}" if current_step else "Starting"
            try:
                indicator.update(Text(f"{icon} {step_text} · {elapsed:.0f}s · {body}"))
            except Exception:
                pass

        status_timer = self.set_interval(1.0, _render_status)
        _render_status()

        if self._warmup_task is not None and not self._warmup_task.done():
            phase = "warming_up"
            _render_status()
            await self._await_warmup()
            phase = "waiting"
            _render_status()

        tokens_before = self.total_tokens
        try:
            async for event in orchestrator.run(messages):
                if event.type == "text":
                    chunk = str(event.content)
                    current_thought += chunk
                    self.total_tokens += 1
                    phase = "writing"
                    _render_status()
                    scroll.scroll_end(animate=False)

                elif event.type == "reasoning":
                    current_reasoning += str(event.content)
                    phase = "reasoning"
                    _render_status()
                    scroll.scroll_end(animate=False)

                elif event.type == "step_start":
                    info = event.content or {}
                    try:
                        current_step = int(info.get("step") or current_step)
                    except (TypeError, ValueError):
                        pass
                    try:
                        max_steps = int(info.get("max_steps") or max_steps)
                    except (TypeError, ValueError):
                        pass
                    current_reasoning = ""
                    current_thought = ""
                    phase = "waiting"
                    _render_status()

                elif event.type == "provider":
                    info = event.content or {}
                    self.last_provider = info.get("name", self.last_provider)
                    self.update_status_bar()

                elif event.type == "tool_start":
                    info = event.content
                    step_num = info.get("step", 1)
                    t_name = info.get("name", "")
                    t_args = info.get("args", {})
                    call_id = info.get("id") or f"{step_num}_{t_name}"

                    current_step = step_num
                    phase = "tool"
                    active_tool = t_name
                    _render_status()
                    card = ToolStepCard(
                        tool_name=t_name,
                        args=t_args,
                        step=step_num,
                        status="running",
                    )
                    active_cards[call_id] = card
                    try:
                        container.mount(card, before=indicator)
                    except Exception:
                        try:
                            container.mount(card)
                        except Exception:
                            pass
                    scroll.scroll_end(animate=False)
                    current_thought = ""

                elif event.type == "tool_finish":
                    info = event.content
                    step_num = info.get("step", 1)
                    t_name = info.get("name", "")
                    res = str(info.get("result", ""))
                    call_id = info.get("id") or f"{step_num}_{t_name}"
                    card = active_cards.get(call_id)
                    ok = bool(info.get("ok", not res.startswith("Error")))
                    if not ok:
                        failed_tools.append((t_name, res))
                    if card:
                        status = "done" if ok else "error"
                        card.update_status(status=status, result=res)
                    phase = "analyzing"
                    active_tool = ""
                    _render_status()
                    scroll.scroll_end(animate=False)

                elif event.type == "usage":
                    last_usage.update(event.content or {})
                    output = last_usage.get("output_tokens")
                    if output:
                        self.total_tokens = max(self.total_tokens, int(output))
                    context_tokens = last_usage.get("input_tokens")
                    if context_tokens:
                        self.context_used = min(int(context_tokens), self.context_max)
                    self.update_status_bar()

                elif event.type == "compaction":
                    info = event.content or {}
                    self.notify(
                        f"Context compacted ({info.get('before_tokens', '?')} → "
                        f"{info.get('after_tokens', '?')} tokens)"
                    )
                    indicator.update("✦ Context compacted, continuing...")

                elif event.type == "compaction_error":
                    self.notify(f"Compaction warning: {event.content}", severity="warning")

                elif event.type == "completion_warning":
                    self.notify(
                        "The agent tried to finish while tool calls were still failing.",
                        severity="warning",
                    )

                elif event.type == "complete":
                    final_answer = str(event.content)

                elif event.type == "error":
                    final_answer = f"**Agent Error:** `{event.content}`"

            elapsed = max(time.monotonic() - started, 0.001)
            self.gen_time = elapsed
            self.last_gen_tokens = self.total_tokens - tokens_before
            self.gen_time_active = elapsed

            try:
                indicator.remove()
            except Exception:
                pass

            now = datetime.now().strftime("%I:%M %p")
            clean_reply = final_answer.strip()
            if not clean_reply and current_thought:
                clean_reply = current_thought.strip()
            if not clean_reply:
                clean_reply = format_incomplete_agent_message(failed_tools)

            self.output_buffer = clean_reply
            self.token_count = len(clean_reply.split())
            self.context_used = min(self.context_used + self.token_count, self.context_max)

            self.write_message("assistant", clean_reply, elapsed=elapsed, timestamp=now)
            # The agent persists its own messages. Only add a fallback if the
            # final text was not already stored (e.g. an empty provider reply).
            stored = self.store.messages(self.session_id)
            last = stored[-1] if stored else None
            if not (
                last
                and last.get("role") == "assistant"
                and last.get("content") == clean_reply
            ):
                self.store.add_message(self.session_id, "assistant", clean_reply)
            self._kick_off_titling()

        except asyncio.CancelledError:
            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", "*[Agent task cancelled]*", timestamp=now)
            self.store.add_message(self.session_id, "assistant", "*[Agent task cancelled]*")
        except Exception as exc:
            try:
                indicator.remove()
            except Exception:
                pass
            now = datetime.now().strftime("%I:%M %p")
            self.write_message("assistant", f"**Agent Exception:** `{exc}`", timestamp=now)
            self.store.add_message(self.session_id, "assistant", f"**Agent Exception:** `{exc}`")
        finally:
            try:
                status_timer.stop()
            except Exception:
                pass
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
            _, reply = self.store.first_exchange(self.session_id)
            title = await suggest_title(self.client, source, reply)
            if title:
                self.store.set_auto_title(self.session_id, title)
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
        elif name == "web":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            server = self.ensure_web_server()
            if server is None:
                self.write_message(
                    "assistant",
                    "Web UI could not start. Check the terminal for the error.",
                    timestamp=now,
                )
            else:
                url = server.new_pairing_url()
                try:
                    await asyncio.to_thread(webbrowser.open, url)
                    opened = True
                except Exception:
                    opened = False
                self.write_message(
                    "assistant",
                    "# Web UI\n\n"
                    f"{'Opened' if opened else 'Open this link'} in your browser:\n\n"
                    f"`{url}`\n\n"
                    f"Server: `{server.base_url}` — shared with this TUI's sessions.\n\n"
                    "The link works once and expires in 5 minutes. "
                    "Run `/web` again for a fresh one.",
                    timestamp=now,
                )
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
        elif name == "permissions":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            sub = args.strip()
            parts = sub.split()
            if not sub or parts[0].lower() == "status":
                self.write_message(
                    "assistant",
                    self.permission_manager.describe()
                    + "\n\nUse `/permissions mode auto|ask|readonly`, "
                    "`/permissions allow <action> <pattern>`, "
                    "`/permissions deny <action> <pattern>`, or `/permissions reset`.",
                    timestamp=now,
                )
            elif parts[0].lower() == "mode" and len(parts) > 1:
                mode = parts[1].lower()
                try:
                    self.settings.save_permission_mode(mode)
                except ValueError as exc:
                    self.write_message("assistant", f"Error: {exc}", timestamp=now)
                    return
                rules: list[PermissionRule] = []
                for raw in self.settings.permission_rules:
                    try:
                        rules.append(parse_rule(raw))
                    except ValueError:
                        continue
                self.permission_manager = PermissionManager(
                    mode=mode,
                    rules=rules,
                    approval=self._ask_permission,
                    session_id=self.session_id,
                )
                self.write_message(
                    "assistant", f"Permission mode set to **{mode}**.", timestamp=now
                )
            elif parts[0].lower() in ("allow", "deny", "ask"):
                if len(parts) < 2:
                    self.write_message(
                        "assistant",
                        "Usage: `/permissions allow|deny|ask <action> <pattern>`",
                        timestamp=now,
                    )
                    return
                effect = parts[0].lower()
                action = parts[1]
                pattern = " ".join(parts[2:]) or "*"
                rule = PermissionRule(action, pattern, effect)
                self.permission_manager.add_rule(rule)
                self.settings.permission_rules.append(str(rule))
                self.settings.save_permission_rules(self.settings.permission_rules)
                self.write_message(
                    "assistant", f"Added permission rule `{rule}`.", timestamp=now
                )
            elif parts[0].lower() == "reset":
                self.permission_manager.user_rules.clear()
                self.permission_manager.reset_session_rules()
                self.settings.save_permission_rules([])
                self.write_message(
                    "assistant", "Permission rules reset to defaults.", timestamp=now
                )
            else:
                self.write_message(
                    "assistant",
                    "Unknown subcommand. Try `/permissions status`, `mode`, `allow`, `deny`, or `reset`.",
                    timestamp=now,
                )
        elif name == "compact":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            compactor = Compactor(
                self.client,
                context_limit=self.settings.context_limit,
                threshold=self.settings.compact_threshold,
                keep_tokens=self.settings.compact_keep_tokens,
            )
            try:
                result = await compactor.compact(
                    self.store.context_messages(self.session_id),
                    reason="manual",
                    instructions=args.strip(),
                )
            except Exception as exc:  # noqa: BLE001
                self.write_message("assistant", f"Compaction failed: `{exc}`", timestamp=now)
                return
            if result is None:
                self.write_message(
                    "assistant", "Nothing to compact yet.", timestamp=now
                )
                return
            self.store.add_message(
                self.session_id,
                "system",
                "<conversation-checkpoint>\n"
                + result.summary
                + "\n</conversation-checkpoint>",
                metadata={"kind": "checkpoint", "reason": "manual"},
            )
            self.store.set_summary(self.session_id, result.summary)
            self.write_message(
                "assistant",
                f"### Context compacted\n\nRemoved {result.removed} messages "
                f"({result.before_tokens} → {result.after_tokens} tokens).\n\n"
                + result.summary,
                timestamp=now,
            )
        elif name == "mcp":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            if self.mcp is None:
                self.write_message(
                    "assistant", "MCP is disabled in settings.", timestamp=now
                )
                return
            sub = args.strip().lower()
            if sub in ("start", "reload"):
                if sub == "reload":
                    await self.mcp.stop()
                    self._mcp_started = False
                await self.mcp.start()
                self._mcp_started = True
            elif sub == "stop":
                await self.mcp.stop()
                self._mcp_started = False
            self.write_message(
                "assistant",
                self.mcp.status()
                + f"\n\nConfig: `{self.settings.mcp_config}`\n"
                "Subcommands: `/mcp status|start|reload|stop`",
                timestamp=now,
            )
        elif name == "skills":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            if self.skills is None:
                self.write_message(
                    "assistant", "Skills are disabled in settings.", timestamp=now
                )
                return
            if args.strip().lower() == "reload":
                self.skills.reload()
            self.write_message(
                "assistant",
                self.skills.summary() or "No skills installed.",
                timestamp=now,
            )
        elif name == "lsp":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            if self.lsp is None:
                self.write_message(
                    "assistant", "LSP is disabled in settings.", timestamp=now
                )
                return
            if args.strip().lower() == "stop":
                await self.lsp.stop()
            self.write_message("assistant", self.lsp.status(), timestamp=now)
        elif name == "provider":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            sub = args.strip().lower()
            if sub in ("auto", "native", "prompt"):
                self.settings.save_provider(sub)
                self.last_provider = sub
                self.write_message(
                    "assistant",
                    f"Provider mode set to **{sub}**.\n"
                    "- `native`: OpenAI-compatible tool calling\n"
                    "- `prompt`: XML tool calls through `/completion`\n"
                    "- `auto`: try native, fall back automatically",
                    timestamp=now,
                )
            else:
                self.write_message(
                    "assistant",
                    f"Provider mode: **{self.settings.provider}**\n"
                    "Use `/provider auto|native|prompt`.",
                    timestamp=now,
                )
            self.update_status_bar()
        elif name == "context":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            if args.strip():
                try:
                    self.settings.save_context_limit(int(args.strip()))
                except ValueError:
                    self.write_message(
                        "assistant", "Usage: `/context <token limit>`", timestamp=now
                    )
                    return
                self.context_max = self.settings.context_limit
            self.write_message(
                "assistant",
                f"Context limit: **{self.settings.context_limit}** tokens\n"
                f"Compact threshold: **{self.settings.compact_threshold:.0%}**\n"
                f"Keep recent: **{self.settings.compact_keep_tokens}** tokens",
                timestamp=now,
            )
            self.update_status_bar()
        elif name == "init":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M %p")
            from pathlib import Path

            target = Path(self.settings.workspace_dir).resolve() / "AGENTS.md"
            if target.exists():
                self.write_message(
                    "assistant",
                    f"`AGENTS.md` already exists at `{target}`.",
                    timestamp=now,
                )
                return
            target.write_text(
                "# Project Instructions\n\n"
                "## Overview\n"
                "Describe the project and its purpose.\n\n"
                "## Commands\n"
                "- Build: `...`\n"
                "- Test: `...`\n"
                "- Lint: `...`\n\n"
                "## Conventions\n"
                "- Language and style\n"
                "- Testing expectations\n"
                "- Files that must not be edited directly\n",
                encoding="utf-8",
            )
            self.write_message(
                "assistant",
                f"Created `{target}`. Edit it to describe this project's conventions.",
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
                    f"- **Tools Available**: `read`, `write`, `edit`, `glob`, `grep`, `bash`, `patch`, `git_*`, `lsp_*`, `skill`, MCP tools\n"
                    f"- **Provider**: `{self.settings.provider}` (last: `{self.last_provider}`)\n"
                    f"- **Permissions**: `{self.settings.permission_mode}`\n"
                    f"- **Context Limit**: `{self.settings.context_limit}` tokens\n"
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

    def _model_label(self, limit: int = 24) -> str:
        """Shorten long model ids so the header stays one line."""
        name = str(self.settings.model or "").strip()
        if len(name) <= limit:
            return name
        if limit <= 1:
            return "…"
        return name[: limit - 1] + "…"

    def _update_model_label(self) -> None:
        """Fit the model name to the space the top bar actually gives it."""
        try:
            slot = self.query_one("#topbar-model", Static).content_size.width
        except Exception:
            slot = 0
        if slot <= 0:
            slot = 24
        if slot >= 22:
            text = f"Model: {self._model_label(max(6, slot - 9))} ˅"
        elif slot >= 5:
            text = self._model_label(slot)
        else:
            text = "…"
        try:
            self.query_one("#topbar-model", Static).update(text)
        except Exception:
            pass

    def update_status_bar(self) -> None:
        self._update_model_label()

        now = datetime.now().strftime("%I:%M:%S %p")
        try:
            self.query_one("#topbar-time", Static).update(now)
        except Exception:
            pass

        if self.last_gen_tokens > 0 and self.gen_time_active > 0:
            tok_s = f"{self.last_gen_tokens / max(self.gen_time_active, 0.01):.1f}"
        else:
            tok_s = "0.0"
        parts = [
            f"context: {self.context_used}/{self.context_max}",
            f"tok/s: {tok_s}",
            f"time: {self.gen_time:.1f}s",
            f"provider: {self.last_provider}",
            f"perm: {self.settings.permission_mode}",
            f"gen: {self.total_gen_count}",
            f"temp: {self.settings.temperature}",
            f"theme: {self.active_theme.name}",
        ]
        status_bar = "  │  ".join(parts)
        # Drop trailing (least important) stats rather than let the line clip.
        try:
            avail = self.query_one("#statusbar", Static).content_size.width
        except Exception:
            avail = 0
        if avail <= 0:
            avail = max(20, self.size.width - 2)
        while len(parts) > 2 and len(status_bar) > avail:
            parts.pop()
            status_bar = "  │  ".join(parts)
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
        self._apply_footer_layout(self.size.width)

    def show_chat(self) -> None:
        self._hide_autocomplete()
        self.query_one("#landing", Container).add_class("hidden")
        self.query_one("#chat-area", Vertical).remove_class("hidden")
        self.query_one("#chat-prompt", ChatInput).focus()
        self._apply_footer_layout(self.size.width)

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
        self.last_gen_tokens = 0
        self.gen_time_active = 0.0
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
                self.action_settings_menu()
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
                self.action_model_picker()
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
