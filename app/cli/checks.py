"""Low-risk checks CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import handle_cli_error, print_check_run_summary
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.services.findings import CheckRunService

app = typer.Typer(help="Run low-risk, explainable checks against authenticated inventory.")
service = CheckRunService()


@app.command("run")
def run_checks(
    inventory_run_id: Annotated[int, typer.Option("--inventory")],
) -> None:
    """Run built-in low-risk checks against an inventory run."""
    try:
        with session_scope() as session:
            typer.echo(f"Running low-risk checks against inventory {inventory_run_id}...")
            summary = service.run_inventory_checks(session, inventory_run_id)
        print_check_run_summary(summary)
    except GardenError as error:
        handle_cli_error(error)
