"""Scan job CLI commands."""

import typer

from app.cli.utils import (
    console,
    format_timestamp,
    handle_cli_error,
    render_key_value,
    render_table,
    styled_status,
)
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
        console.print("[dim]No scan jobs found.[/dim]")
        return

    headers = ["ID", "Target", "Cred", "Status", "Started"]
    rows = [
        [
            str(j.id),
            str(j.target_id),
            str(j.credential_profile_id),
            styled_status(j.status),
            format_timestamp(j.started_at),
        ]
        for j in jobs
    ]
    render_table(headers, rows, title="Scan Jobs")


@app.command("show")
def show_job(job_id: int) -> None:
    """Show scan job details."""
    try:
        with session_scope() as session:
            job = service.get(session, job_id)

        rows: list[tuple[str, str]] = [
            ("Target ID", str(job.target_id)),
            ("Credential Profile ID", str(job.credential_profile_id)),
            ("Status", styled_status(job.status)),
            ("Started", format_timestamp(job.started_at)),
            ("Finished", format_timestamp(job.finished_at)),
            ("Summary", job.summary or "-"),
            ("Error", job.error_message or "-"),
        ]
        render_key_value(rows, title=f"Scan Job #{job.id}")
    except GardenError as error:
        handle_cli_error(error)
