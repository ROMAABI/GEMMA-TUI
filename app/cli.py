import atexit
import subprocess
import time

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


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session", "-s", help="Open an existing session id."),
    model: str | None = typer.Option(None, "--model", "-m", help="Use a model alias."),
) -> None:
    if ctx.invoked_subcommand:
        return

    settings = Settings.load()
    if model:
        settings.model = model

    if not _ensure_server(settings.base_url):
        console.print("[yellow]Warning: llama.cpp server is not running. [/yellow]")

    atexit.register(stop_llama_server)
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
