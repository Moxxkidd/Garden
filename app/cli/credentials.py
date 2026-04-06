"""Credential profile CLI commands."""

from typing import Annotated

import typer

from app.cli.utils import handle_cli_error
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.models.enums import AuthType
from app.redaction.display import redact_secret_ref
from app.schemas.credential import CredentialProfileCreate
from app.services.credentials import CredentialProfileService

app = typer.Typer(help="Manage credential profiles.")
service = CredentialProfileService()


@app.command("add")
def add_credential(
    target_id: Annotated[int, typer.Option("--target-id")],
    name: Annotated[str, typer.Option("--name")],
    role: Annotated[str, typer.Option("--role")],
    auth_type: Annotated[AuthType, typer.Option("--auth-type")],
    username: Annotated[str, typer.Option("--username")],
    secret_ref: Annotated[str, typer.Option("--secret-ref")],
    login_config_path: Annotated[str, typer.Option("--login-config-path")],
) -> None:
    """Add a credential profile."""
    try:
        payload = CredentialProfileCreate(
            target_id=target_id,
            name=name,
            role=role,
            auth_type=auth_type,
            username=username,
            secret_ref=secret_ref,
            login_config_path=login_config_path,
        )
        with session_scope() as session:
            credential = service.create(session, payload)
        typer.echo(f"Created credential profile #{credential.id}: {credential.name}")
    except GardenError as error:
        handle_cli_error(error)


@app.command("list")
def list_credentials() -> None:
    """List all credential profiles."""
    with session_scope() as session:
        credentials = service.list(session)
    if not credentials:
        typer.echo("No credential profiles found.")
        return
    typer.echo(f"{'ID':<4} {'TARGET':<8} {'NAME':<18} {'AUTH':<10} USERNAME")
    for credential in credentials:
        typer.echo(
            f"{credential.id:<4} {credential.target_id:<8} {credential.name[:18]:<18} "
            f"{credential.auth_type:<10} {credential.username}"
        )


@app.command("show")
def show_credential(credential_id: int) -> None:
    """Show credential profile details."""
    try:
        with session_scope() as session:
            credential = service.get(session, credential_id)
        typer.echo(f"Credential Profile #{credential.id}")
        typer.echo(f"Target ID: {credential.target_id}")
        typer.echo(f"Name: {credential.name}")
        typer.echo(f"Role: {credential.role}")
        typer.echo(f"Auth Type: {credential.auth_type}")
        typer.echo(f"Username: {credential.username}")
        typer.echo(f"Secret Ref: {redact_secret_ref(credential.secret_ref)}")
        typer.echo(f"Login Config Path: {credential.login_config_path}")
        typer.echo(f"Created: {credential.created_at.isoformat()}")
        typer.echo(f"Updated: {credential.updated_at.isoformat()}")
    except GardenError as error:
        handle_cli_error(error)


@app.command("delete")
def delete_credential(
    credential_id: int,
    yes: bool = typer.Option(False, "--yes", help="Delete without confirmation."),
) -> None:
    """Delete a credential profile."""
    if not yes and not typer.confirm(f"Delete credential profile {credential_id}?"):
        raise typer.Exit(code=0)
    try:
        with session_scope() as session:
            service.delete(session, credential_id)
        typer.echo(f"Deleted credential profile {credential_id}.")
    except GardenError as error:
        handle_cli_error(error)
