"""Auth session CLI commands."""

from __future__ import annotations

import json

import typer

from app.cli.utils import format_timestamp, handle_cli_error
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
            typer.echo("No sessions found.")
            return
        typer.echo(f"{'ID':<4} {'TARGET':<18} {'PROFILE':<18} {'MODE':<24} STATUS")
        for auth_session in sessions:
            target_name = target_service.get(session, auth_session.target_id).name
            profile_name = credential_service.get(session, auth_session.credential_profile_id).name
            typer.echo(
                f"{auth_session.id:<4} {target_name[:18]:<18} {profile_name[:18]:<18} "
                f"{auth_session.session_type:<24} {auth_session.status}"
            )


@app.command("show")
def show_session(session_id: int) -> None:
    """Show stored authenticated session details."""
    try:
        with session_scope() as session:
            auth_session = service.get(session, session_id)
            target_name = target_service.get(session, auth_session.target_id).name
            profile_name = credential_service.get(session, auth_session.credential_profile_id).name
        typer.echo(f"Session #{auth_session.id}")
        typer.echo(f"Target: {target_name}")
        typer.echo(f"Profile: {profile_name}")
        typer.echo(f"Status: {auth_session.status}")
        typer.echo(f"Session Type: {auth_session.session_type}")
        typer.echo(f"Created: {format_timestamp(auth_session.created_at)}")
        typer.echo(f"Expires: {format_timestamp(auth_session.expires_at)}")
        typer.echo(f"Last Validated: {format_timestamp(auth_session.last_validated_at)}")
        typer.echo(f"Refresh Supported: {'yes' if auth_session.refresh_supported else 'no'}")
        typer.echo(f"Storage Ref: {auth_session.storage_ref}")
        typer.echo("Metadata:")
        typer.echo(
            json.dumps(redact_session_metadata(auth_session.session_metadata_redacted), indent=2)
        )
        typer.echo(f"Last Error: {auth_session.last_error or '-'}")
    except GardenError as error:
        handle_cli_error(error)


@app.command("refresh")
def refresh_session(session_id: int) -> None:
    """Refresh a stored authenticated session."""
    try:
        with session_scope() as session:
            result = service.refresh(session, session_id)
        typer.echo(f"Session {session_id}")
        typer.echo(f"Status: {result.status.value}")
        typer.echo(f"Message: {result.message}")
        if result.failure_reason:
            typer.echo(f"Failure Reason: {result.failure_reason.value}")
        if result.last_error:
            typer.echo(f"Last Error: {result.last_error}")
        if not result.success:
            raise typer.Exit(code=1)
    except GardenError as error:
        handle_cli_error(error)
