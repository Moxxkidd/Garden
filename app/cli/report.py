"""Report CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import handle_cli_error
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.services.reporting import ReportService

app = typer.Typer(help="Generate concise workflow reports.")
service = ReportService()


@app.command("generate")
def generate_report(
    job_id: Annotated[int, typer.Option("--job")],
    export_format: Annotated[str, typer.Option("--format")] = "md",
) -> None:
    """Generate a redacted job report."""
    try:
        with session_scope() as session:
            result = service.generate(session, job_id=job_id, export_format=export_format)
        typer.echo(f"Generated report for job {result.job_id} at {result.output_path}")
    except GardenError as error:
        handle_cli_error(error)
