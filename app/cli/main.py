"""Typer CLI entrypoint for Garden."""

import os
from typing import Annotated

import typer

from app.cli.doctor import doctor as doctor_command
from app.cli.lazy_group import GardenCommandGroup
from app.cli.utils import console, render_json
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.db.bootstrap import init_database
from app.services.health import build_health_response

app = typer.Typer(
    cls=GardenCommandGroup,
    name="garden",
    help=(
        "CLI for Garden's one-URL automatic asset reports and advanced authenticated "
        "verification workflows."
    ),
    no_args_is_help=True,
    invoke_without_command=True,
)


@app.callback()
def main(
    context: typer.Context,
    show_version: Annotated[
        bool,
        typer.Option("--version", help="Show the current Garden version.", is_eager=True),
    ] = False,
) -> None:
    if context.invoked_subcommand == "doctor" and not show_version:
        return
    settings = get_settings()
    if show_version:
        console.print(f"[bold]{settings.project_name}[/bold] {settings.project_version}")
        raise typer.Exit
    if context.invoked_subcommand == "version":
        return
    configure_logging(settings.log_level)
    init_database(settings.database_url)


@app.command()
def version() -> None:
    """Show the current Garden version."""
    settings = get_settings()
    console.print(f"[bold]{settings.project_name}[/bold] {settings.project_version}")


@app.command()
def healthcheck() -> None:
    """Run a local application health snapshot without starting the API server."""
    response = build_health_response()
    render_json(response.model_dump(), title="Health Check")


app.command("doctor")(doctor_command)


if __name__ == "__main__":
    app(prog_name=os.environ.get("GARDEN_CLI_NAME", "garden"))
