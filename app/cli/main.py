"""Typer CLI entrypoint for Garden."""

import os
from typing import Annotated

import typer

from app.cli.baseline import app as baseline_app
from app.cli.checks import app as checks_app
from app.cli.coverage import coverage as coverage_command
from app.cli.credentials import app as credential_app
from app.cli.database import app as database_app
from app.cli.evidence import app as evidence_app
from app.cli.findings import app as findings_app
from app.cli.inventory import app as inventory_app
from app.cli.jobs import app as job_app
from app.cli.login import app as login_app
from app.cli.report import app as report_app
from app.cli.retest import app as retest_app
from app.cli.scan import scan as scan_command
from app.cli.sessions import app as session_app
from app.cli.stop import stop as stop_command
from app.cli.targets import app as target_app
from app.cli.utils import console, render_json
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.db.bootstrap import init_database
from app.services.health import build_health_response

app = typer.Typer(
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


app.command("scan")(scan_command)
app.command("coverage")(coverage_command)
app.command("stop")(stop_command)
app.add_typer(target_app, name="target")
app.add_typer(credential_app, name="cred")
app.add_typer(job_app, name="job")
app.add_typer(login_app, name="login")
app.add_typer(session_app, name="session")
app.add_typer(inventory_app, name="inventory")
app.add_typer(checks_app, name="checks")
app.add_typer(findings_app, name="findings")
app.add_typer(evidence_app, name="evidence")
app.add_typer(retest_app, name="retest")
app.add_typer(report_app, name="report")
app.add_typer(baseline_app, name="baseline")
app.add_typer(database_app, name="db")


if __name__ == "__main__":
    app(prog_name=os.environ.get("GARDEN_CLI_NAME", "garden"))
