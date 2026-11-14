import atexit
import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

import typer
from rich.console import Console

from app.lifecycle import server_is_healthy, stop_llama_server
from config.settings import Settings
from sessions.store import SessionStore
from tui.app import GemmaTUI

app = typer.Typer(no_args_is_help=False, add_completion=False)
console = Console()


def _ensure_server(base_url: str) -> bool:
    """Start the llama.cpp server if it is not already running."""
    if server_is_healthy(base_url):
        return True
    try:
        subprocess.run(
            ["systemctl", "--user", "start", "gemma-llama-server"],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    for _ in range(60):
        if server_is_healthy(base_url):
            return True
        time.sleep(1)
    return False


TUI_DIR = Path(__file__).resolve().parent.parent / "tui-ts"


def _find_bun() -> str | None:
    """Locate the Bun runtime used by the OpenTUI client."""
    found = shutil.which("bun")
    if found:
        return found
    fallback = Path.home() / ".bun" / "bin" / "bun"
    return str(fallback) if fallback.exists() else None


def _opentui_ready(bun: str | None) -> bool:
    return bool(bun) and (TUI_DIR / "src" / "index.tsx").exists() and (TUI_DIR / "node_modules").exists()


def _run_opentui(settings: Settings, session_id: str | None = None) -> bool:
    """Start the shared server and hand the terminal to the OpenTUI client.

    Returns False when the client cannot run, so the caller can fall back to the
    Textual TUI instead of leaving the user with no interface.
    """
    bun = _find_bun()
    if not _opentui_ready(bun):
        if bun is None:
            console.print("[yellow]Bun was not found, so the OpenTUI client cannot start.[/yellow]")
        else:
            console.print(
                "[yellow]The OpenTUI client is not installed. Run "
                "[/yellow][cyan]cd tui-ts && bun install[/cyan][yellow].[/yellow]"
            )
        return False

    from core.webui import GemmaWebServer

    server = GemmaWebServer(settings)
    base = server.start()
    pair = server.new_pairing_url()

    env = os.environ.copy()
    env["GEMMA_URL"] = base
    env["GEMMA_PAIR_URL"] = pair
    if session_id:
        env["GEMMA_SESSION_ID"] = session_id

    started = time.monotonic()
    try:
        completed = subprocess.run([bun, "run", "src/index.tsx"], cwd=TUI_DIR, env=env)
    except KeyboardInterrupt:
        completed = None
    finally:
        server.stop()

    if completed is None:
        return True
    # A crash before the first frame means the client is unusable here; let the
    # caller fall back to the classic TUI.
    return completed.returncode == 0 or (time.monotonic() - started) > 2.0


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session", "-s", help="Open an existing session id."),
    model: str | None = typer.Option(None, "--model", "-m", help="Use a model alias."),
    classic: bool = typer.Option(
        False,
        "--classic",
        help="Use the Textual TUI (agent mode, permissions and MCP) instead of OpenTUI.",
    ),
) -> None:
    if ctx.invoked_subcommand:
        return

    settings = Settings.load()
    if model:
        settings.model = model

    was_running = server_is_healthy(settings.base_url)
    if not _ensure_server(settings.base_url):
        console.print("[yellow]Warning: llama.cpp server is not running. [/yellow]")
    elif not was_running:
        # Only stop what this process started; a pre-existing llama.cpp server
        # (or one owned by another tool) stays up after the TUI exits.
        atexit.register(stop_llama_server)

    if not classic and _run_opentui(settings, session):
        return
    if not classic:
        console.print("[dim]Falling back to the classic TUI (gemma --classic).[/dim]")

    GemmaTUI(settings=settings, initial_session_id=session).run()


@app.command()
def sessions() -> None:
    """List saved chat sessions."""
    store = SessionStore()
    rows = store.list_sessions()
    if not rows:
        console.print("No sessions yet.")
        return

    for row in rows:
        console.print(f"{row.id}  {row.title}  {row.updated_at}")


@app.command()
def web(
    port: int = typer.Option(47317, "--port", "-p", help="Port for the web UI server."),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the pairing link in a browser."
    ),
) -> None:
    """Serve the web UI and print a one-time pairing link."""
    settings = Settings.load()
    if not _ensure_server(settings.base_url):
        console.print("[yellow]Warning: llama.cpp server is not running. [/yellow]")

    from core.webui import GemmaWebServer

    server = GemmaWebServer(settings, host=host, port=port)
    url = server.start()
    pair = server.new_pairing_url()
    console.print(f"[bold green]Gemma web UI[/bold green] serving at [cyan]{url}[/cyan]")
    console.print(f"Pair: [cyan]{pair}[/cyan]")
    console.print("[dim]Links work once and expire in 5 minutes. Ctrl+C to stop.[/dim]")
    if open_browser:
        webbrowser.open(pair)
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


@app.command()
def config() -> None:
    """Show resolved Gemma app config."""
    settings = Settings.load()
    console.print_json(data=settings.as_dict())


def _display_models(settings: Settings) -> None:
    from rich.table import Table
    from models.registry import get_available_models

    available = get_available_models()
    table = Table(title="Gemma Models", show_header=True, header_style="bold magenta")
    table.add_column("Status", style="bold", width=12)
    table.add_column("Model Name", style="cyan")
    table.add_column("File / Description", style="dim")

    # De-duplicate entries that point to the same model file
    seen_files = set()
    for name, profile in available.items():
        file_key = profile.filename or name
        if file_key in seen_files:
            continue
        seen_files.add(file_key)

        is_active = (
            name == settings.model
            or profile.filename == settings.model
            or (profile.filename and settings.model in (name, profile.filename))
        )
        status = "[green]● Active[/green]" if is_active else "[dim]○ Available[/dim]"
        table.add_row(status, name, profile.filename or profile.description or "alias")

    console.print(table)
    console.print(
        f"\n[dim]Active model:[/dim] [bold cyan]{settings.model}[/bold cyan]\n"
        f"[dim]To switch model:[/dim] [bold cyan]gemma model <name>[/bold cyan] or [bold cyan]gemma switch <name>[/bold cyan]\n"
    )


def _switch_model(name: str) -> None:
    from models.registry import get_available_models

    settings = Settings.load()
    available = get_available_models()

    target_name = name
    matched_profile = None
    for k, profile in available.items():
        if k.lower() == name.lower() or (profile.filename and profile.filename.lower() == name.lower()):
            target_name = profile.name
            matched_profile = profile
            break

    settings.save_model(target_name)
    console.print(f"[bold green]✓[/bold green] Active model switched to: [bold cyan]{target_name}[/bold cyan]")
    if matched_profile and matched_profile.filename:
        console.print(f"[dim]  Model file: models/{matched_profile.filename}[/dim]")
    elif not matched_profile:
        console.print(f"[yellow]  Notice: '{name}' is not in models/ directory. Saved as custom model name.[/yellow]")


@app.command()
def model(
    name: str | None = typer.Argument(
        None, help="Model name or filename to switch to. Omit to list available models."
    )
) -> None:
    """Show active model, list available models, or switch model."""
    if name:
        _switch_model(name)
    else:
        settings = Settings.load()
        _display_models(settings)


@app.command()
def models() -> None:
    """List all available models and indicate which is active."""
    settings = Settings.load()
    _display_models(settings)


@app.command()
def switch(
    name: str = typer.Argument(..., help="Model name or filename to switch to.")
) -> None:
    """Switch the active model."""
    _switch_model(name)
