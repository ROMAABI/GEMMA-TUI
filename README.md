# ◆  GEMMA TUI

A rich terminal user interface for local LLM chat powered by [Textual](https://textual.textualize.io/). Designed for use with [llama.cpp](https://github.com/ggml-org/llama.cpp) server.

![Dark theme](https://img.shields.io/badge/theme-dark-purple?style=flat)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue?style=flat)
![Textual](https://img.shields.io/badge/textual-1.0.0-orange?style=flat)

---

## Features

- **Dracula-inspired dark theme** with purple accents — easy on the eyes for long sessions
- **Multi-line chat input** — Enter to send, Shift+Enter for newline
- **Slash command autocomplete** — type `/` to see and tab-complete commands
- **Command palette** — `Ctrl+P` to search and run commands
- **Chat sessions** — persistent history with rename, delete, and switch
- **Markdown rendering** — messages render rich Markdown (code blocks, headers, lists)
- **Live streaming** — see responses token-by-token as they're generated
- **Model switching** — switch model aliases at runtime with `/model <name>`
- **Keyboard-first** — all interactions available via keyboard shortcuts

---

## Installation

### Prerequisites

- **Python 3.11+**
- **llama.cpp server** running locally (or any OpenAI-compatible endpoint)

### Install

```bash
# Clone the repository
git clone git@github.com:ROMAABI/GEMMA-TUI.git
cd GEMMA-TUI

# Install with pip
pip install .
```

Or in editable mode for development:

```bash
pip install -e .
```

### Start llama.cpp server

```bash
# Download a model (e.g. Gemma 3 4B IT Q4_K_M)
wget -O models/gemma-3-4b-it-Q4_K_M.gguf https://huggingface.co/bartowski/gemma-3-4b-it-Q4_K_M.gguf

# Start the server
llama-server -m models/gemma-3-4b-it-Q4_K_M.gguf --port 8080
```

---

## Usage

```bash
# Launch the TUI
gemma-local

# Or via main.py
python main.py
```

### CLI Options

| Flag | Description |
|---|---|
| `--session`, `-s <id>` | Open an existing session by ID |
| `--model`, `-m <name>` | Start with a specific model alias |

### Subcommands

```bash
gemma-local sessions    # List saved chat sessions
gemma-local config      # Show resolved configuration
```

---

## Commands

| Command | Description |
|---|---|
| `/help` | Show commands and keyboard shortcuts |
| `/model [name]` | Show or switch the model alias |
| `/clear` | Clear the visible chat area |
| `/new` | Start a new chat session |
| `/delete` | Delete the current session |
| `/rename <name>` | Rename the current session |
| `/history` | List all saved sessions |
| `/stats` | Show current session statistics |
| `/config` | Display the resolved configuration |
| `/exit` | Quit the application |

### Keyboard Shortcuts

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift+Enter` | Insert newline in input |
| `Ctrl+Enter` | Send message (alternate) |
| `Ctrl+P` | Open command palette |
| `Ctrl+N` | New chat session |
| `Ctrl+L` | Clear chat |
| `Ctrl+C` | Cancel streaming response |
| `Ctrl+D` | Quit |
| `Delete` | Delete current session |
| `Escape` | Close autocomplete popup |

---

## Configuration

Configuration is loaded from a TOML file. Defaults:

```toml
[server]
base_url = "http://127.0.0.1:8080"

[model]
name = "gemma"
temperature = 0.7

[ui]
title = "Gemma Local Assistant"
name = "Abi"
```

### Config file locations

- **Linux:** `~/.config/gemma-local/settings.toml`
- **macOS:** `~/Library/Application Support/gemma-local/settings.toml`

Override defaults by creating a settings file at the platform-appropriate path.

---

## Project Structure

```
GEMMA-TUI/
├── app/
│   ├── cli.py          # CLI entrypoint (typer)
│   └── lifecycle.py    # Server lifecycle management
├── config/
│   ├── defaults.toml   # Default configuration
│   └── settings.py     # Settings loading/resolution
├── core/
│   ├── client.py       # Llama.cpp HTTP client (streaming)
│   └── commands.py     # Command definitions & parsing
├── sessions/
│   ├── schema.py       # Database schema
│   └── store.py        # Session persistence (SQLite)
├── tui/
│   ├── app.py          # Main TUI — all widgets, CSS, handlers
│   └── __init__.py
├── utils/
│   └── paths.py        # Platform-aware config paths
├── models/             # Symlink or download GGUF models here
├── main.py             # Quick entrypoint
└── pyproject.toml      # Package metadata & dependencies
```

---

## Architecture

The TUI is built with [Textual](https://textual.textualize.io/) and follows a straightforward single-app pattern:

1. **`GemmaTUI`** — the `App` subclass containing all widget composition, CSS, and event handlers (~1300 lines)
2. **`LlamaClient`** — async HTTP streaming client sending requests to the llama.cpp server
3. **`SessionStore`** — SQLite-backed persistence for chat history across sessions
4. **`Settings`** — TOML-based configuration with platform-appropriate user overrides

Chat messages are rendered as `ChatMessage` widgets (custom `Static` subclass) with Markdown support via Rich. Streaming responses render token-by-token in a live indicator, then finalize into a fully rendered message.

### MountError Workaround

Textual 1.0.0 raises a `MountError` when `mount()` is called during a message handler (CSS pipeline defers changes until the handler completes). This is handled with a `_mount_or_queue` pattern that retries via a `set_interval` timer:

```python
def _mount_or_queue(self, parent, widget):
    try:
        parent.mount(widget)
    except MountError:
        self._pending_mounts.append((parent, widget))
```

The timer flushes queued mounts at 50ms intervals, outside the handler context where `mount()` succeeds.

---

## Development

```bash
# Editable install
pip install -e .

# Run directly (no install)
python main.py

# Launch with Textual devtools for live CSS reloading
textual run tui/app.py --dev
```
