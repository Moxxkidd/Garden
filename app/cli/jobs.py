"""Scan job CLI commands."""

import typer

from app.cli.utils import format_timestamp, handle_cli_error
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.services.jobs import ScanJobService

app = typer.Typer(help="Browse scan jobs.")
service = ScanJobService()


@app.command("list")
def list_jobs() -> None:
    """List scan jobs."""
    with session_scope() as session:
        jobs = service.list(session)
    if not jobs:
        typer.echo("No scan jobs found.")
        return
    typer.echo(f"{'ID':<4} {'TARGET':<8} {'CRED':<8} {'STATUS':<12} STARTED")
    for job in jobs:
        typer.echo(
            f"{job.id:<4} {job.target_id:<8} {job.credential_profile_id:<8} "
            f"{job.status:<12} {format_timestamp(job.started_at)}"
        )


@app.command("show")
def show_job(job_id: int) -> None:
    """Show scan job details."""
    try:
        with session_scope() as session:
            job = service.get(session, job_id)
        typer.echo(f"Scan Job #{job.id}")
        typer.echo(f"Target ID: {job.target_id}")
        typer.echo(f"Credential Profile ID: {job.credential_profile_id}")
        typer.echo(f"Status: {job.status}")
        typer.echo(f"Started: {format_timestamp(job.started_at)}")
        typer.echo(f"Finished: {format_timestamp(job.finished_at)}")
        typer.echo(f"Summary: {job.summary or '-'}")
        typer.echo(f"Error: {job.error_message or '-'}")
    except GardenError as error:
        handle_cli_error(error)
