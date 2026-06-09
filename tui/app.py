from __future__ import annotations

import atexit
import asyncio
import getpass
import os
import time
from datetime import datetime

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive, var
from textual.screen import ModalScreen, Screen
from textual.widget import MountError, Widget
from textual.widgets import Input, Label, ListItem, ListView, Static, SelectionList, TextArea

from app.lifecycle import stop_llama_server, server_is_healthy
from config.settings import Settings
from core.client import LlamaClient
from core.commands import COMMAND_DEFS, HELP_TEXT, parse_command
from sessions.store import SessionStore


SYSTEM_PROMPT = "You are a helpful assistant. Reply directly without thinking or reasoning steps."


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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.role = role
        self.msg_content = content
        self.elapsed = elapsed
        self.msg_timestamp = timestamp or datetime.now().strftime("%I:%M:%S %p")
        self.add_class(f"role-{role}")

    def compose(self) -> ComposeResult:
        username = _get_username()
        if self.role == "user":
            with Horizontal(classes="msg-container"):
                yield Static(username[0].upper(), classes="avatar user-avatar")
                with Vertical(classes="msg-body"):
                    with Horizontal(classes="msg-header"):
                        yield Static(username, classes="msg-name user-name")
                        yield Static(self.msg_timestamp, classes="msg-time")
                    yield Static(self.msg_content, classes="msg-text")
        else:
            with Horizontal(classes="msg-container"):
                yield Static("G", classes="avatar gemma-avatar")
                with Vertical(classes="msg-body"):
                    with Horizontal(classes="msg-header"):
                        yield Static("Gemma", classes="msg-name gemma-name")
                        yield Static(self.msg_timestamp, classes="msg-time")
                    yield Static(Markdown(self.msg_content), classes="msg-text")
                    if self.elapsed is not None:
                        yield Static(f"{self.elapsed:.1f}s", classes="elapsed-badge")


class ChatInput(TextArea):
    """TextArea that submits on Enter, inserts newline on Shift+Enter."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            await super()._on_key(event)
            return
        await super()._on_key(event)


class CodeBlock(Static):
    """A code block with language label and copy button."""

    def __init__(self, code: str, language: str = "python", elapsed: float | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.code = code
        self.language = language
        self.elapsed = elapsed

    def compose(self) -> ComposeResult:
        with Vertical(classes="code-block"):
            with Horizontal(classes="code-header"):
                yield Static(f"  {self.language} ˅", classes="code-lang")
                yield Static("⎘", classes="code-copy")
            yield Static(self.code, classes="code-content", markup=False)
            if self.elapsed is not None:
                yield Static(f" {self.elapsed:.1f}s ", classes="code-elapsed")


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


class SidebarItem(Static):
    """A clickable sidebar item with optional active indicator."""

    def __init__(self, label: str, *, active: bool = False, icon: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.label_text = label
        self.is_active = active
        self.icon = icon

    def compose(self) -> ComposeResult:
        dot = "● " if self.is_active else "  "
        yield Static(f"{dot}{self.icon} {self.label_text}", classes="sidebar-link")


# ── Command Palette Screen ────────────────────────────────────────────


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
        background: $background;
    }
    """

    commands: list[tuple[str, str]] = COMMAND_DEFS

    def compose(self) -> ComposeResult:
        with Vertical(id="command-palette"):
            yield Input(placeholder="Search commands...", id="command-search")
            yield ListView(id="command-list")

    def on_mount(self) -> None:
        self._populate_commands()
        self.query_one("#command-search", Input).focus()

    def _populate_commands(self, filter_text: str = "") -> None:
        lst = self.query_one("#command-list", ListView)
        lst.clear()
        for cmd, desc in self.commands:
            if filter_text.lower() in cmd.lower() or filter_text.lower() in desc.lower():
                lst.append(CommandItem(cmd, desc))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command-search":
            self._populate_commands(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "command-list":
            self.dismiss(event.item.command if isinstance(event.item, CommandItem) else None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-search":
            lst = self.query_one("#command-list", ListView)
            if lst.children:
                self.dismiss(lst.children[0].command if isinstance(lst.children[0], CommandItem) else None)
            else:
                self.dismiss(None)


# ── Main TUI Application ───────────────────────────────────────────────


class GemmaTUI(App):
    CSS = """
    Screen {
        background: #0B0F17;
        color: #E5E7EB;
    }

    * {
        scrollbar-size-vertical: 1;
        scrollbar-color: #1F2937;
        scrollbar-color-hover: #3B82F6;
        scrollbar-color-active: #bd93f9;
    }

    /* ── Shell Layout ─────────────────────────────── */

    #shell {
        height: 1fr;
        width: 1fr;
    }

    /* ── Sidebar ──────────────────────────────────── */

    #sidebar {
        width: 32;
        background: #111827;
        border-right: solid #1F2937;
        padding: 2 0;
    }

    #brand {
        height: 3;
        padding: 0 2;
        content-align: left middle;
        text-style: bold;
        color: #bd93f9;
        margin-bottom: 2;
    }

    #nav-title {
        color: #6272A4;
        text-style: bold;
        padding: 0 2;
        height: 2;
        margin-top: 1;
        margin-bottom: 1;
    }

    #nav-new {
        height: 3;
        padding: 0 2;
        margin: 0 0 0 0;
        color: #E5E7EB;
    }

    #nav-new:hover {
        background: #1F2937;
    }

    #nav-chats {
        height: 3;
        padding: 0 2;
        margin: 0 0 0 0;
        color: #E5E7EB;
    }

    #nav-chats:hover {
        background: #1F2937;
    }

    #recents-title {
        color: #6272A4;
        text-style: bold;
        padding: 0 2;
        height: 2;
        margin-top: 2;
        margin-bottom: 1;
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
        padding: 0 2;
        color: #94A3B8;
        border: none;
    }

    #sessions > ListItem:hover {
        background: #1F2937;
        color: #E5E7EB;
    }

    #sessions > ListItem.--highlight {
        background: #1F2937;
        color: #E5E7EB;
    }

    #sessions > ListItem.active-session {
        background: #1A1F2E;
        color: #bd93f9;
        text-style: bold;
    }

    /* ── Workspace ────────────────────────────────── */

    #workspace {
        width: 1fr;
        height: 1fr;
        background: #0B0F17;
    }

    /* ── Top Bar ──────────────────────────────────── */

    #topbar {
        height: 3;
        background: #0B0F17;
        border-bottom: solid #1F2937;
        padding: 0 2;
    }

    #topbar-left {
        width: 1fr;
        height: 3;
        content-align: left middle;
        color: #E5E7EB;
        text-style: none;
    }

    #topbar-right {
        width: auto;
        height: 3;
        content-align: right middle;
    }

    #model-status {
        width: auto;
        height: 3;
        content-align: right middle;
        margin-right: 2;
    }

    #topbar-time {
        width: auto;
        height: 3;
        content-align: right middle;
        color: #94A3B8;
        margin-right: 2;
    }

    #topbar-help {
        width: auto;
        height: 3;
        content-align: right middle;
        color: #6272A4;
    }

    /* ── Chat Area ────────────────────────────────── */

    #chat-scroll {
        height: 1fr;
        padding: 1 4;
    }

    #chat-messages {
        width: 1fr;
        height: auto;
        padding: 0;
    }

    /* ── Message Styles ───────────────────────────── */

    .msg-container {
        width: 1fr;
        height: auto;
        margin-bottom: 1;
        padding: 1 0;
    }

    .avatar {
        width: 4;
        height: 4;
        content-align: center middle;
        text-style: bold;
        margin-right: 1;
    }

    .user-avatar {
        background: #3B82F6;
        color: #FFFFFF;
    }

    .gemma-avatar {
        background: #9333EA;
        color: #FFFFFF;
    }

    .msg-body {
        width: 1fr;
        height: auto;
    }

    .msg-header {
        height: 1;
        margin-bottom: 0;
    }

    .msg-name {
        width: auto;
        text-style: bold;
        margin-right: 2;
    }

    .user-name {
        color: #3B82F6;
    }

    .gemma-name {
        color: #9333EA;
    }

    .msg-time {
        color: #6272A4;
        text-style: none;
    }

    .msg-text {
        color: #E5E7EB;
        padding: 0;
    }

    .elapsed-badge {
        width: auto;
        height: 1;
        margin-top: 1;
        color: #6272A4;
        background: transparent;
        text-style: italic;
    }

    /* ── Code Block Styles ────────────────────────── */

    .code-block {
        width: 1fr;
        height: auto;
        background: #111827;
        border: solid #1F2937;
        margin: 1 0;
    }

    .code-header {
        width: 1fr;
        height: 1;
        background: #1F2937;
        padding: 0 1;
    }

    .code-lang {
        width: 1fr;
        color: #E5E7EB;
        text-style: bold;
    }

    .code-copy {
        width: auto;
        color: #94A3B8;
    }

    .code-content {
        padding: 1 2;
        color: #E5E7EB;
    }

    .code-elapsed {
        width: auto;
        height: 1;
        color: #94A3B8;
        content-align: right middle;
        margin: 0 1;
        text-style: italic;
    }

    /* ── Input Area ───────────────────────────────── */

    #input-area {
        height: 10;
        padding: 1 4;
        background: #0B0F17;
    }

    #input-card {
        width: 1fr;
        height: 8;
        background: #111827;
        border: solid #1F2937;
        padding: 1;
    }

    #chat-prompt {
        height: 6;
        background: transparent;
        border: none;
        color: #E5E7EB;
        padding: 0;
    }

    #chat-prompt:focus {
        border: none;
    }

    #input-controls {
        height: 1;
        padding: 0 1;
    }

    #send-btn {
        width: 1fr;
        content-align: right middle;
        color: #A855F7;
        text-style: bold;
    }

    #send-btn:hover {
        color: #D8B4FE;
    }

    /* ── Status Bar ───────────────────────────────── */

    #statusbar {
        height: 1;
        background: #111827;
        border-top: solid #1F2937;
        padding: 0 2;
        content-align: left middle;
        color: #6272A4;
    }

    /* ── Landing Page ─────────────────────────────── */

    #landing {
        height: 1fr;
        align: center middle;
        background: #0B0F17;
    }

    #landing-inner {
        width: 72;
        height: auto;
        align: center middle;
    }

    #landing-brand {
        height: 4;
        content-align: center middle;
        text-style: bold;
        color: #bd93f9;
        margin-bottom: 2;
    }

    #prompt-card {
        width: 80;
        height: 4;
        background: #111827;
        border: solid #1F2937;
        padding: 0 1;
    }

    #prompt {
        height: 3;
        background: transparent;
        border: none;
        color: #E5E7EB;
    }

    #prompt:focus {
        border: none;
    }

    /* ── Streaming Indicator ──────────────────────── */

    #streaming-indicator {
        width: 1fr;
        height: auto;
        margin-top: 1;
        color: #3B82F6;
        text-style: italic;
    }

    /* ── Command Palette ──────────────────────────── */

    #command-palette-screen {
        align: center top;
    }

    #command-palette {
        width: 60;
        height: auto;
        margin-top: 6;
        background: #111827;
        border: solid #1F2937;
        padding: 0;
    }

    #command-search {
        height: 3;
        background: #0B0F17;
        border: none;
        color: #E5E7EB;
        padding: 0 1;
    }

    #command-search:focus {
        border: none;
    }

    #command-list {
        height: auto;
        max-height: 20;
        background: #111827;
        border: none;
        padding: 0;
        margin: 0;
    }

    #command-list > ListItem {
        height: 2;
        padding: 0 1;
        color: #E5E7EB;
    }

    #command-list > ListItem:hover {
        background: #1F2937;
    }

    #command-list > ListItem.--highlight {
        background: #1F2937;
    }

    .command-key {
        color: #3B82F6;
        text-style: bold;
        width: auto;
    }

    .command-desc {
        color: #94A3B8;
        width: 1fr;
    }

    .command-arrow {
        color: #1F2937;
        width: auto;
    }

    /* ── Slash Autocomplete Popup ─────────────────── */

    #autocomplete-popup {
        width: 40;
        height: auto;
        max-height: 14;
        background: #111827;
        border: solid #1F2937;
        padding: 0;
        margin: 0;
        layer: overlay;
        display: none;
    }

    #autocomplete-popup.visible {
        display: block;
    }

    #autocomplete-items {
        height: auto;
        max-height: 12;
        background: #111827;
        border: none;
        padding: 0;
        margin: 0;
    }

    #autocomplete-items > ListItem {
        height: 2;
        padding: 0 1;
        color: #E5E7EB;
    }

    #autocomplete-items > ListItem:hover {
        background: #1F2937;
    }

    #autocomplete-items > ListItem.--highlight {
        background: #1F2937;
    }

    .autocomplete-key {
        color: #3B82F6;
        text-style: bold;
        width: auto;
    }

    .autocomplete-desc {
        color: #6272a4;
        width: 1fr;
    }

    /* ── Hidden ────────────────────────────────────── */

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        ("ctrl+n", "new_session", "New"),
        ("ctrl+l", "clear_chat", "Clear"),
        ("ctrl+c", "cancel_stream", "Cancel"),
        ("ctrl+d", "quit", "Exit"),
        ("delete", "delete_session", "Delete"),
        ("ctrl+p", "command_palette", "Commands"),
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
        # Ensure llama.cpp server is killed when the process exits
        atexit.register(stop_llama_server)

    def compose(self) -> ComposeResult:
        with Horizontal(id="shell"):
            # ── Sidebar ──
            with Vertical(id="sidebar"):
                yield Static("◆  GEMMA TUI", id="brand")
                yield Static("NAVIGATION", id="nav-title")
                yield Static("+  New Chat", id="nav-new")
                yield Static("💬  Chats", id="nav-chats")
                yield Static("RECENT CHATS", id="recents-title")
                yield ListView(id="sessions")

            # ── Workspace ──
            with Vertical(id="workspace"):
                # Top bar
                with Horizontal(id="topbar"):
                    yield Static(f"MODEL: {self.settings.model}", id="topbar-left")
                    with Horizontal(id="topbar-right"):
                        yield Static("●  CONNECTED", id="model-status")
                        yield Static("", id="topbar-time")
                        yield Static("?", id="topbar-help")

                # Landing page
                with Container(id="landing"):
                    with Vertical(id="landing-inner"):
                        yield Static("◆  GEMMA TUI", id="landing-brand")
                        with Vertical(id="prompt-card"):
                            yield Input(
                                placeholder="Ask Gemma anything... (type / for commands)",
                                id="prompt",
                            )

                # Chat area (hidden by default)
                with Vertical(id="chat-area", classes="hidden"):
                    with VerticalScroll(id="chat-scroll"):
                        yield Vertical(id="chat-messages")
                    with Container(id="input-area"):
                        with Vertical(id="input-card"):
                            yield ChatInput(
                                id="chat-prompt",
                                soft_wrap=True,
                                show_line_numbers=False,
                            )
                            with Horizontal(id="input-controls"):
                                yield Static("➤", id="send-btn")
                    with Vertical(id="autocomplete-popup"):
                        yield ListView(id="autocomplete-items")

                # Status bar
                yield Static("", id="statusbar")

    def on_mount(self) -> None:
        self.title = self.settings.title
        self._session_ids: list[str] = []
        self.refresh_sessions()
        self.load_current_session()
        self.update_status_bar()
        self._app_ready = True
        self.query_one("#prompt", Input).focus()
        # Live health check on startup and every 30s
        self.check_server_health()
        self.set_interval(30, self.check_server_health)
        self._flush_timer = self.set_interval(0.05, self._flush_pending_mounts)
        self.set_interval(30, self._update_clock)

    def _update_clock(self) -> None:
        try:
            now = datetime.now().strftime("%I:%M:%S %p")
            self.query_one("#topbar-time", Static).update(now)
        except Exception:
            pass

    def check_server_health(self) -> None:
        """Check llama.cpp server status and update indicator."""
        healthy = server_is_healthy(self.settings.base_url)
        self.model_loaded = healthy
        status_widget = self.query_one("#model-status", Static)
        if healthy:
            status_widget.update("●  CONNECTED")
            status_widget.styles.color = "#50fa7b"
        else:
            status_widget.update("○  DISCONNECTED")
            status_widget.styles.color = "#ff5555"
        self.update_status_bar()

    # ── Session Management ──────────────────────────────────────────────

    def refresh_sessions(self) -> None:
        sessions_view = self.query_one("#sessions", ListView)
        sessions_view.clear()
        rows = self.store.list_sessions()
        self._session_ids = [row.id for row in rows]
        for row in rows:
            active = row.id == self.session_id
            marker = "● " if active else "  "
            label_text = f"{marker}{row.title[:28]}"
            li = ListItem(Label(label_text))
            if active:
                li.add_class("active-session")
            sessions_view.append(li)

    def load_current_session(self) -> None:
        container = self.query_one("#chat-messages", Vertical)
        container.remove_children()
        messages = self.store.messages(self.session_id)
        if not messages:
            self.show_landing()
            return

        self.show_chat()
        for message in messages:
            self.write_message(message["role"], message["content"])

    # ── Message Display ─────────────────────────────────────────────────

    def write_message(
        self,
        role: str,
        content: str,
        elapsed: float | None = None,
        timestamp: str | None = None,
    ) -> None:
        container = self.query_one("#chat-messages", Vertical)

        # Check if content contains code blocks
        if role == "assistant" and "```" in content:
            self._write_message_with_code(container, content, elapsed, timestamp)
        else:
            msg = ChatMessage(
                role,
                content,
                elapsed=elapsed if role == "assistant" else None,
                timestamp=timestamp,
            )
            self._mount_or_queue(container, msg)

        # Auto-scroll to bottom
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=True)

    def _write_message_with_code(
        self,
        container: Vertical,
        content: str,
        elapsed: float | None,
        timestamp: str | None,
    ) -> None:
        """Parse and render a message that may contain code blocks."""
        parts = content.split("```")
        text_parts = []
        code_parts = []

        for i, part in enumerate(parts):
            if i % 2 == 0:
                # Text segment
                stripped = part.strip()
                if stripped:
                    text_parts.append(("text", stripped))
            else:
                # Code segment - extract language
                lines = part.split("\n", 1)
                lang = lines[0].strip() if lines[0].strip() else "text"
                code = lines[1].strip() if len(lines) > 1 else ""
                text_parts.append(("code", (lang, code)))

        # Render pre-code text as a Gemma message
        pre_text = []
        for kind, data in text_parts:
            if kind == "text":
                pre_text.append(data)
            else:
                # Flush any accumulated text
                if pre_text:
                    msg = ChatMessage(
                        "assistant",
                        "\n".join(pre_text),
                        timestamp=timestamp,
                    )
                    self._mount_or_queue(container, msg)
                    pre_text = []
                # Mount code block
                lang, code = data
                block = CodeBlock(code, language=lang, elapsed=elapsed)
                self._mount_or_queue(container, block)

        if pre_text:
            msg = ChatMessage(
                "assistant",
                "\n".join(pre_text),
                elapsed=elapsed if not code_parts else None,
                timestamp=timestamp,
            )
            self._mount_or_queue(container, msg)

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
        if event.input.id == "prompt":
            val = event.value
            if val.startswith("/") and len(val) > 1 and " " not in val:
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
        ta = event.text_area
        if ta.id == "chat-prompt":
            val = ta.text
            if val.startswith("/") and len(val) > 1 and " " not in val:
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
            return
        self.show_chat()
        await asyncio.sleep(0)
        now = datetime.now().strftime("%I:%M:%S %p")
        self.write_message("user", text, timestamp=now)
        self.store.add_message(self.session_id, "user", text)
        self.refresh_sessions()
        self.streaming_task = asyncio.create_task(self.stream_reply())

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        await self._process_input(text)

    async def action_submit_chat(self) -> None:
        ta = self.query_one("#chat-prompt", ChatInput)
        text = ta.text
        ta.text = ""
        await self._process_input(text)

    # ── Streaming ───────────────────────────────────────────────────────

    async def stream_reply(self) -> None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.store.messages(
            self.session_id
        )
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

        try:
            async for chunk in self.client.stream_chat(messages):
                self.output_buffer += chunk
                self.total_tokens += 1
                # Update indicator with live text
                indicator.update(f"✦ {self.output_buffer}")
                scroll.scroll_end(animate=True)

            elapsed = max(time.monotonic() - started, 0.001)
            self.gen_time = elapsed
            self.token_count = len(self.output_buffer.split())
            self.context_used = min(
                self.context_used + self.token_count, self.context_max
            )

            # Remove indicator and show final message
            indicator.remove()
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message("assistant", self.output_buffer, elapsed=elapsed, timestamp=now)
            self.store.add_message(self.session_id, "assistant", self.output_buffer)

        except asyncio.CancelledError:
            indicator.remove()
            if self.output_buffer:
                now = datetime.now().strftime("%I:%M:%S %p")
                self.write_message(
                    "assistant", self.output_buffer + "\n\n*[cancelled]*", timestamp=now
                )
        except Exception as exc:
            indicator.remove()
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message("assistant", f"**Error:** `{exc}`", timestamp=now)
        finally:
            self.is_streaming = False
            self.update_status_bar()

    # ── Commands ────────────────────────────────────────────────────────

    async def run_command(self, name: str, args: str) -> None:
        if name == "help":
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message("assistant", HELP_TEXT.strip(), timestamp=now)
        elif name == "model":
            self.show_chat()
            await asyncio.sleep(0)
            if args:
                self.settings.model = args
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message(
                "assistant",
                f"Current model: `{self.settings.model}`",
                timestamp=now,
            )
            self.update_status_bar()
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
                now = datetime.now().strftime("%I:%M:%S %p")
                self.write_message("assistant", "Usage: `/rename <new name>`", timestamp=now)
            else:
                self.store.rename_session(self.session_id, args)
                self.refresh_sessions()
                self.show_chat()
                now = datetime.now().strftime("%I:%M:%S %p")
                self.write_message(
                    "assistant",
                    f"Session renamed to `{args}`",
                    timestamp=now,
                )
        elif name == "history":
            self.show_chat()
            await asyncio.sleep(0)
            lines = "\n".join(
                f"- `{row.id}` {row.title}" for row in self.store.list_sessions()
            )
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message(
                "assistant",
                f"# Sessions\n{lines or 'No sessions yet.'}",
                timestamp=now,
            )
        elif name == "stats":
            self.show_chat()
            await asyncio.sleep(0)
            count = self.store.count_messages(self.session_id)
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message(
                "assistant",
                f"Session: {self.session_id}\nMessages: {count}\nModel: {self.settings.model}\nTokens generated: {self.total_tokens}",
                timestamp=now,
            )
        elif name == "config":
            self.show_chat()
            await asyncio.sleep(0)
            data = self.settings.as_dict()
            lines = "\n".join(f"  {key}: {value}" for key, value in data.items())
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message("assistant", f"Config:\n{lines}", timestamp=now)
        elif name == "exit":
            self.exit()
        else:
            self.show_chat()
            await asyncio.sleep(0)
            now = datetime.now().strftime("%I:%M:%S %p")
            self.write_message(
                "assistant",
                f"Unknown command: /{name}. Type /help.",
                timestamp=now,
            )

    # ── Status Bar ──────────────────────────────────────────────────────

    def update_status_bar(self) -> None:
        try:
            self.query_one("#topbar-left", Static).update(f"MODEL: {self.settings.model}")
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
            f"/help for commands"
        )
        self.query_one("#statusbar", Static).update(status_bar)

    # ── View Switching ──────────────────────────────────────────────────

    def show_landing(self) -> None:
        self.query_one("#landing", Container).remove_class("hidden")
        self.query_one("#chat-area", Vertical).add_class("hidden")
        self.query_one("#prompt", Input).focus()

    def show_chat(self) -> None:
        self.query_one("#landing", Container).add_class("hidden")
        self.query_one("#chat-area", Vertical).remove_class("hidden")
        self.query_one("#chat-prompt", ChatInput).focus()

    # ── Actions ─────────────────────────────────────────────────────────

    def action_command_palette(self) -> None:
        """Open the command palette modal."""
        def on_cmd(result: str | None) -> None:
            if result:
                ta = self.query_one("#chat-prompt", ChatInput)
                ta.text = result
                asyncio.create_task(self.action_submit_chat())
        self.push_screen(CommandPalette(), on_cmd)

    def action_close_autocomplete(self) -> None:
        self._hide_autocomplete()

    def _show_autocomplete(self, filter_text: str) -> None:
        if not getattr(self, '_app_ready', False):
            return
        popup = self.query_one("#autocomplete-popup", Vertical)
        if not popup.is_mounted:
            return
        lst = self.query_one("#autocomplete-items", ListView)
        if not lst.is_mounted:
            return
        lst.clear()
        for cmd, desc in self.commands:
            if filter_text.lower() in cmd.lower():
                lst.append(AutoCompleteItem(cmd, desc))
        if lst.children:
            lst.index = 0
            popup.styles.display = "block"
            self._autocomplete_visible = True
        else:
            self._hide_autocomplete()

    def _hide_autocomplete(self) -> None:
        if not getattr(self, '_app_ready', False):
            return
        popup = self.query_one("#autocomplete-popup", Vertical)
        if not popup.is_mounted:
            return
        popup.styles.display = "none"
        self._autocomplete_visible = False

    def action_new_session(self) -> None:
        self.session_id = self.store.create_session()
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
        """Delete the current session and switch to another or create new."""
        old_id = self.session_id
        self.store.delete_session(old_id)
        remaining = self.store.list_sessions()
        if remaining:
            self.session_id = remaining[0].id
        else:
            self.session_id = self.store.create_session()
        self.refresh_sessions()
        self.load_current_session()
        self.update_status_bar()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "autocomplete-items" and isinstance(event.item, AutoCompleteItem):
            self.query_one("#chat-prompt", ChatInput).text = event.item.command + " "
            self._hide_autocomplete()
            self.query_one("#chat-prompt", ChatInput).focus()
            asyncio.create_task(self.action_submit_chat())
        elif event.list_view.id == "sessions":
            idx = event.list_view.index
            if idx is not None and idx < len(self._session_ids):
                self.session_id = self._session_ids[idx]
                self.refresh_sessions()
                self.load_current_session()
                self.update_status_bar()

    def on_static_click(self, event: Static.Click) -> None:
        """Handle clicks on sidebar nav items."""
        widget_id = event.static.id
        if widget_id == "nav-new":
            self.action_new_session()
        elif widget_id == "send-btn":
            asyncio.create_task(self.action_submit_chat())

