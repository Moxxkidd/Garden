"""Target CLI commands."""

from pathlib import Path
from typing import Annotated

import typer

from app.cli.utils import format_tags, handle_cli_error
from app.core.errors import GardenError
from app.db.bootstrap import session_scope
from app.models.enums import TargetStatus, TargetType
from app.schemas.target import TargetCreate
from app.services.targets import DuplicateAction, TargetService

app = typer.Typer(help="Manage targets.")
service = TargetService()


@app.command("add")
def add_target(
    name: Annotated[str, typer.Option("--name")],
    base_url: Annotated[str, typer.Option("--base-url")],
    target_type: Annotated[TargetType, typer.Option("--type")],
    owner: Annotated[str, typer.Option("--owner")],
    tags: Annotated[list[str] | None, typer.Option("--tag")] = None,
    status: Annotated[TargetStatus, typer.Option("--status")] = TargetStatus.ACTIVE,
) -> None:
    """Add a target."""
    try:
        payload = TargetCreate(
            name=name,
            base_url=base_url,
            type=target_type,
            owner=owner,
            tags=tags or [],
            status=status,
        )
        with session_scope() as session:
            target = service.create(session, payload)
        typer.echo(f"Created target #{target.id}: {target.name}")
    except GardenError as error:
        handle_cli_error(error)


@app.command("list")
def list_targets() -> None:
    """List all targets."""
    with session_scope() as session:
        targets = service.list(session)
    if not targets:
        typer.echo("No targets found.")
        return
    typer.echo(f"{'ID':<4} {'NAME':<20} {'TYPE':<10} {'STATUS':<10} OWNER")
    for target in targets:
        typer.echo(
            f"{target.id:<4} {target.name[:20]:<20} "
            f"{target.type:<10} {target.status:<10} {target.owner}"
        )


@app.command("show")
def show_target(target_id: int) -> None:
    """Show target details."""
    try:
        with session_scope() as session:
            target = service.get(session, target_id)
        typer.echo(f"Target #{target.id}")
        typer.echo(f"Name: {target.name}")
        typer.echo(f"Base URL: {target.base_url}")
        typer.echo(f"Type: {target.type}")
        typer.echo(f"Owner: {target.owner}")
        typer.echo(f"Tags: {format_tags(target.tags)}")
        typer.echo(f"Status: {target.status}")
        typer.echo(f"Created: {target.created_at.isoformat()}")
        typer.echo(f"Updated: {target.updated_at.isoformat()}")
    except GardenError as error:
        handle_cli_error(error)


@app.command("delete")
def delete_target(
    target_id: int,
    yes: bool = typer.Option(False, "--yes", help="Delete without confirmation."),
) -> None:
    """Delete a target."""
    if not yes and not typer.confirm(f"Delete target {target_id}?"):
        raise typer.Exit(code=0)
    try:
        with session_scope() as session:
            service.delete(session, target_id)
        typer.echo(f"Deleted target {target_id}.")
    except GardenError as error:
        handle_cli_error(error)


@app.command("import")
def import_targets(
    file_path: Annotated[
        Path,
        typer.Option("--file", exists=True, readable=True, resolve_path=True),
    ],
    on_duplicate: Annotated[DuplicateAction, typer.Option("--on-duplicate")] = (
        DuplicateAction.SKIP
    ),
) -> None:
    """Import targets from YAML or JSON."""
    try:
        with session_scope() as session:
            result = service.import_file(session, file_path, on_duplicate=on_duplicate)
        typer.echo(
            "Import complete: "
            f"imported={result.imported} updated={result.updated} skipped={result.skipped}"
        )
    except GardenError as error:
        handle_cli_error(error)
