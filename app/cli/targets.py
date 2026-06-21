"""Target CLI commands."""

from pathlib import Path
from typing import Annotated

import typer

from app.cli.utils import (
    console,
    format_tags,
    format_timestamp,
    handle_cli_error,
    render_key_value,
    render_table,
    styled_status,
)
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
        console.print(f"[green]Created target #{target.id}:[/green] {target.name}")
    except GardenError as error:
        handle_cli_error(error)


@app.command("list")
def list_targets() -> None:
    """List all targets."""
    with session_scope() as session:
        targets = service.list(session)
    if not targets:
        console.print("[dim]No targets found.[/dim]")
        return

    headers = ["ID", "Name", "Type", "Status", "Owner"]
    rows = [[str(t.id), t.name, t.type, styled_status(t.status), t.owner] for t in targets]
    render_table(headers, rows, title="Targets")


@app.command("show")
def show_target(target_id: int) -> None:
    """Show target details."""
    try:
        with session_scope() as session:
            target = service.get(session, target_id)

        rows: list[tuple[str, str]] = [
            ("Name", target.name),
            ("Base URL", target.base_url),
            ("Type", target.type),
            ("Owner", target.owner),
            ("Tags", format_tags(target.tags)),
            ("Status", styled_status(target.status)),
            ("Created", format_timestamp(target.created_at)),
            ("Updated", format_timestamp(target.updated_at)),
        ]
        render_key_value(rows, title=f"Target #{target.id}")
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
        console.print(f"[green]Deleted target {target_id}.[/green]")
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
        console.print(
            f"[green]Import complete:[/green] "
            f"imported={result.imported}  updated={result.updated}  skipped={result.skipped}"
        )
    except GardenError as error:
        handle_cli_error(error)
