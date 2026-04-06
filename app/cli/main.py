"""Typer CLI entrypoint for Garden."""

import json

import typer

from app.cli.checks import app as checks_app
from app.cli.credentials import app as credential_app
from app.cli.evidence import app as evidence_app
from app.cli.findings import app as findings_app
from app.cli.inventory import app as inventory_app
from app.cli.jobs import app as job_app
from app.cli.login import app as login_app
from app.cli.report import app as report_app
from app.cli.retest import app as retest_app
from app.cli.sessions import app as session_app
from app.cli.targets import app as target_app
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.db.bootstrap import init_database
from app.services.health import build_health_response

app = typer.Typer(
    name="gardenctl",
    help=(
        "CLI for Garden authenticated verification workflows: target, login, "
        "session, inventory, checks, findings, evidence, retest, and report."
    ),
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_database(settings.database_url)


@app.command()
def version() -> None:
    """Show the current Garden version."""
    settings = get_settings()
    typer.echo(f"{settings.project_name} {settings.project_version}")


@app.command()
def healthcheck() -> None:
    """Run a local application health snapshot without starting the API server."""
    response = build_health_response()
    typer.echo(json.dumps(response.model_dump(), indent=2))


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


if __name__ == "__main__":
    app()
