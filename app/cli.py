import typer
from rich.console import Console

from config.settings import Settings
from sessions.store import SessionStore
from tui.app import GemmaTUI

app = typer.Typer(no_args_is_help=False, add_completion=False)
console = Console()


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
