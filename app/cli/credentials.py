"""Credential profile CLI commands."""

from typing import Annotated

import typer

from app.cli.utils import (
    console,
    format_timestamp,
    handle_cli_error,
    render_key_value,
    render_table,
)
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
        console.print(
            f"[green]Created credential profile #{credential.id}:[/green] {credential.name}"
        )
    except GardenError as error:
        handle_cli_error(error)


@app.command("list")
def list_credentials() -> None:
    """List all credential profiles."""
    with session_scope() as session:
        credentials = service.list(session)
    if not credentials:
        console.print("[dim]No credential profiles found.[/dim]")
        return

    headers = ["ID", "Target", "Name", "Auth Type", "Username"]
    rows = [
        [str(c.id), str(c.target_id), c.name, c.auth_type, c.username]
        for c in credentials
    ]
    render_table(headers, rows, title="Credential Profiles")


@app.command("show")
def show_credential(credential_id: int) -> None:
    """Show credential profile details."""
    try:
        with session_scope() as session:
            credential = service.get(session, credential_id)

        rows: list[tuple[str, str]] = [
            ("Target ID", str(credential.target_id)),
            ("Name", credential.name),
            ("Role", credential.role),
            ("Auth Type", credential.auth_type),
            ("Username", credential.username),
            ("Secret Ref", redact_secret_ref(credential.secret_ref)),
            ("Login Config Path", credential.login_config_path),
            ("Created", format_timestamp(credential.created_at)),
            ("Updated", format_timestamp(credential.updated_at)),
        ]
        render_key_value(rows, title=f"Credential Profile #{credential.id}")
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
        console.print(f"[green]Deleted credential profile {credential_id}.[/green]")
    except GardenError as error:
        handle_cli_error(error)
