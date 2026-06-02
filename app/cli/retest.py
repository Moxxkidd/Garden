"""Retest CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import (
    handle_cli_error,
    progress_spinner,
    render_key_value,
    styled_status,
)
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
            with progress_spinner("Running retest ..."):
                summary = service.run(session, finding_id=finding_id)

        rows: list[tuple[str, str]] = [
            ("Finding", str(summary.finding_id)),
            ("Status", styled_status(summary.status)),
            ("Reused Session", "[green]yes[/green]" if summary.reused_session else "[dim]no[/dim]"),
            ("Inventory Run", str(summary.inventory_run_id or "-")),
            ("Scan Job", str(summary.scan_job_id or "-")),
            ("Session", str(summary.session_id or "-")),
            ("Summary", summary.summary),
        ]
        render_key_value(rows, title="Retest Result")
    except GardenError as error:
        handle_cli_error(error)
