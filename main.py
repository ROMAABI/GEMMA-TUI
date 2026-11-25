"""Gemma entry point.

The Bun/Solid client in tui-ts/ is the only terminal UI. On first launch the
CLI installs its dependencies via scripts/install_tui_deps.sh, then starts the
shared server and hands the terminal to the TypeScript client. The Python
(Textual) TUI is no longer part of the launch path.
"""

from app.cli import app


if __name__ == "__main__":
    app()
