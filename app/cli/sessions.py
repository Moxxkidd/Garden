"""Auth session CLI commands."""

from __future__ import annotations

import typer

from app.cli.utils import (
    console,
    format_timestamp,
    handle_cli_error,
    render_json,
    render_key_value,
    render_table,
    styled_status,
)
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.redaction.display import redact_session_metadata
from app.services.credentials import CredentialProfileService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService

app = typer.Typer(help="Browse and refresh authenticated sessions.")
service = AuthSessionService()
target_service = TargetService()
credential_service = CredentialProfileService()


@app.command("list")
def list_sessions() -> None:
    """List stored authenticated sessions."""
    with session_scope() as session:
        sessions = service.list(session)
        if not sessions:
            console.print("[dim]No sessions found.[/dim]")
            return

        headers = ["ID", "Target", "Profile", "Mode", "Status"]
        rows = []
        for auth_session in sessions:
            target_name = target_service.get(session, auth_session.target_id).name
            profile_name = credential_service.get(session, auth_session.credential_profile_id).name
            rows.append(
                [
                    str(auth_session.id),
                    target_name,
                    profile_name,
                    auth_session.session_type,
                    styled_status(auth_session.status),
                ]
            )
        render_table(headers, rows, title="Authenticated Sessions")


@app.command("show")
def show_session(session_id: int) -> None:
    """Show stored authenticated session details."""
    try:
        with session_scope() as session:
            auth_session = service.get(session, session_id)
            target_name = target_service.get(session, auth_session.target_id).name
            profile_name = credential_service.get(session, auth_session.credential_profile_id).name

        rows: list[tuple[str, str]] = [
            ("Target", target_name),
            ("Profile", profile_name),
            ("Status", styled_status(auth_session.status)),
            ("Session Type", auth_session.session_type),
            ("Created", format_timestamp(auth_session.created_at)),
            ("Expires", format_timestamp(auth_session.expires_at)),
            ("Last Validated", format_timestamp(auth_session.last_validated_at)),
            (
                "Refresh Supported",
                "[green]yes[/green]" if auth_session.refresh_supported else "[dim]no[/dim]",
            ),
            ("Storage Ref", auth_session.storage_ref),
            ("Last Error", auth_session.last_error or "-"),
        ]
        render_key_value(rows, title=f"Session #{auth_session.id}")

        if auth_session.session_metadata_redacted:
            render_json(
                redact_session_metadata(auth_session.session_metadata_redacted),
                title="Session Metadata",
            )

    except GardenError as error:
        handle_cli_error(error)


@app.command("refresh")
def refresh_session(session_id: int) -> None:
    """Refresh a stored authenticated session."""
    try:
        with session_scope() as session:
            result = service.refresh(session, session_id)

        rows: list[tuple[str, str]] = [
            ("Session", str(session_id)),
            ("Status", styled_status(result.status.value)),
            ("Message", result.message),
        ]
        if result.failure_reason:
            rows.append(("Failure Reason", result.failure_reason.value))
        if result.last_error:
            rows.append(("Last Error", result.last_error))
        render_key_value(rows, title="Session Refresh")

        if not result.success:
            raise typer.Exit(code=1)

    except GardenError as error:
        handle_cli_error(error)
