import atexit
import contextlib
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from app.lifecycle import server_is_healthy, stop_llama_server
from config.settings import Settings
from sessions.store import SessionStore
from utils.paths import server_log_path

app = typer.Typer(no_args_is_help=False, add_completion=False)
console = Console()


def _ensure_server(base_url: str) -> bool:
    """Start the llama.cpp server if it is not already running.

    A service that exits immediately (for example because its GGUF file is
    missing) must not cost a full minute of polling on every launch.
    """
    if server_is_healthy(base_url):
        return True
    try:
        started = subprocess.run(
            ["systemctl", "--user", "start", "gemma-llama-server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if started.returncode != 0:
        return False
    for _ in range(60):
        if server_is_healthy(base_url):
            return True
        try:
            state = subprocess.run(
                ["systemctl", "--user", "show", "-p", "SubState", "--value", "gemma-llama-server"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            state = ""
        # `auto-restart` means the service already crashed and systemd is
        # retrying (a missing model, for example), so health will never come.
        if state in {"failed", "auto-restart"}:
            return False
        time.sleep(1)
    return False


ROOT_DIR = Path(__file__).resolve().parent.parent
TUI_DIR = ROOT_DIR / "tui-ts"
INSTALL_SCRIPT = ROOT_DIR / "scripts" / "install_tui_deps.sh"


@dataclass
class _LogRedirect:
    """Result of ``_stdout_to_log``: where logs go and where the tty still is."""

    log_path: Path
    tty_fd: int


@contextlib.contextmanager
def _stdout_to_log():
    """Send the process's stdout/stderr to the server log while active.

    The web server runs in this same process, so anything it prints would
    paint straight over the fullscreen TUI. File descriptors 1 and 2 are
    pointed at the log file (which also catches every subprocess the server
    spawns); the TUI child keeps the real terminal via the dup'd tty fd.
    """
    log_path = server_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tty_fd = os.dup(1)
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)
    try:
        yield _LogRedirect(log_path=log_path, tty_fd=tty_fd)
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        os.dup2(tty_fd, 1)
        os.dup2(tty_fd, 2)
        os.close(tty_fd)


def _find_bun() -> str | None:
    """Locate the Bun runtime used by the OpenTUI client."""
    found = shutil.which("bun")
    if found:
        return found
    fallback = Path.home() / ".bun" / "bin" / "bun"
    return str(fallback) if fallback.exists() else None


def _opentui_ready(bun: str | None) -> bool:
    return bool(bun) and (TUI_DIR / "src" / "index.tsx").exists() and (TUI_DIR / "node_modules").exists()


def _ensure_tui_deps(bun: str) -> bool:
    """Run the Bun installer on first launch, then verify the client is ready."""
    try:
        completed = subprocess.run(
            ["bash", str(INSTALL_SCRIPT)],
            env={**os.environ, "BUN": bun},
            capture_output=True,
            text=True,
        )
    except OSError as error:
        console.print(f"[red]Could not run the OpenTUI installer:[/red] {error}")
        return False
    if completed.returncode != 0:
        console.print("[red]Could not install the OpenTUI client dependencies.[/red]")
        detail = (completed.stderr or completed.stdout).strip()
        if detail:
            console.print(f"[dim]{detail}[/dim]")
        return False
    return _opentui_ready(bun)


def _run_opentui(
    settings: Settings,
    session_id: str | None = None,
    *,
    continue_last: bool = False,
) -> bool:
    """Start the shared server and hand the terminal to the OpenTUI client.

    Returns False only when the client could not be started at all; there is no
    Python TUI fallback.
    """
    bun = _find_bun()
    if bun is None:
        console.print("[red]Bun is required for the OpenTUI client. Install it from https://bun.sh[/red]")
        return False
    if not _ensure_tui_deps(bun):
        return False

    from core.webui import GemmaWebServer

    with _stdout_to_log() as redirect:
        server = GemmaWebServer(settings)
        base = server.start()
        pair = server.new_pairing_url()

        env = os.environ.copy()
        env["GEMMA_URL"] = base
        env["GEMMA_PAIR_URL"] = pair
        if session_id:
            env["GEMMA_SESSION_ID"] = session_id
        elif continue_last:
            env["GEMMA_CONTINUE"] = "1"

        started = time.monotonic()
        try:
            # Explicit fds: bun renders on the real tty even though this
            # process's own stdout/stderr now point at the log file.
            completed = subprocess.run(
                [bun, "run", "src/index.tsx"],
                cwd=TUI_DIR,
                env=env,
                stdout=redirect.tty_fd,
                stderr=redirect.tty_fd,
            )
        except KeyboardInterrupt:
            completed = None
        finally:
            server.stop()

        if completed is None:
            return True
        # Treat a crash before the first frame as a failure to launch; anything
        # that survived longer than two seconds is a normal quit.
        return completed.returncode == 0 or (time.monotonic() - started) > 2.0


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session", "-s", help="Open an existing session id."),
    continue_last: bool = typer.Option(
        False, "--continue", "-c", help="Open the last session instead of a fresh New chat."
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Use a model alias."),
) -> None:
    if ctx.invoked_subcommand:
        return

    settings = Settings.load()
    if model:
        settings.model = model

    was_running = server_is_healthy(settings.base_url)
    if not _ensure_server(settings.base_url):
        console.print(
            "[yellow]Warning: llama.cpp server is not running — chat is unavailable. "
            "Check [/yellow][cyan]systemctl --user status gemma-llama-server[/cyan] "
            "[yellow](and that models/*.gguf exists).[/yellow]"
        )
    elif not was_running:
        # Only stop what this process started; a pre-existing llama.cpp server
        # (or one owned by another tool) stays up after the TUI exits.
        atexit.register(stop_llama_server)

    if not _run_opentui(settings, session, continue_last=continue_last):
        raise typer.Exit(code=1)


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
