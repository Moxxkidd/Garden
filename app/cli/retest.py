"""Retest CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import handle_cli_error
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.services.retest import RetestService

app = typer.Typer(help="Retest findings using current workflow context where possible.")
service = RetestService()


@app.command("run")
def run_retest(
    finding_id: Annotated[int, typer.Option("--finding")],
) -> None:
    """Run a retest for a finding in fixed or retest-pending state."""
    try:
        with session_scope() as session:
            summary = service.run(session, finding_id=finding_id)
        typer.echo(f"Finding: {summary.finding_id}")
        typer.echo(f"Status: {summary.status}")
        typer.echo(f"Reused Session: {'yes' if summary.reused_session else 'no'}")
        typer.echo(f"Inventory Run: {summary.inventory_run_id or '-'}")
        typer.echo(f"Scan Job: {summary.scan_job_id or '-'}")
        typer.echo(f"Session: {summary.session_id or '-'}")
        typer.echo(f"Summary: {summary.summary}")
    except GardenError as error:
        handle_cli_error(error)
