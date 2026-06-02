"""Login orchestration CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import (
    handle_cli_error,
    print_login_result,
    progress_spinner,
    render_key_value,
    styled_status,
)
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.services.sessions import AuthSessionService

app = typer.Typer(help="Run login tests and validation.")
service = AuthSessionService()


@app.command("test")
def login_test(
    target_name: Annotated[str, typer.Option("--target")],
    profile_name: Annotated[str, typer.Option("--profile")],
) -> None:
    """Test a configured login flow and create a reusable session on success."""
    try:
        with session_scope() as session:
            with progress_spinner("Running login test ..."):
                result = service.login_test(session, target_name, profile_name)
        print_login_result(result)
        if not result.success:
            raise typer.Exit(code=1)
    except GardenError as error:
        handle_cli_error(error)


@app.command("validate")
def login_validate(session_id: Annotated[int, typer.Option("--session")]) -> None:
    """Validate an existing session."""
    try:
        with session_scope() as session:
            result = service.validate(session, session_id)

        rows: list[tuple[str, str]] = [
            ("Session", str(session_id)),
            ("Status", styled_status(result.status.value)),
            ("Valid", "[green]yes[/green]" if result.valid else "[red]no[/red]"),
            ("Message", result.message),
        ]
        if result.failure_reason:
            rows.append(("Failure Reason", result.failure_reason.value))
        if result.last_error:
            rows.append(("Last Error", result.last_error))
        render_key_value(rows, title="Session Validation")

        if not result.valid:
            raise typer.Exit(code=1)

    except GardenError as error:
        handle_cli_error(error)
