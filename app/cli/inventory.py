"""Inventory CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import handle_cli_error, print_inventory_summary
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.inventory import InventoryBuildControls
from app.services.credentials import CredentialProfileService
from app.services.inventory import InventoryBuildService
from app.services.targets import TargetService

app = typer.Typer(help="Build and browse authenticated inventory.")
service = InventoryBuildService()
target_service = TargetService()
credential_service = CredentialProfileService()


@app.command("build")
def build_inventory(
    target_name: Annotated[str | None, typer.Option("--target")] = None,
    profile_name: Annotated[str | None, typer.Option("--profile")] = None,
    session_id: Annotated[int | None, typer.Option("--session")] = None,
    max_pages: Annotated[int, typer.Option("--max-pages")] = 25,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 2,
    max_requests: Annotated[int, typer.Option("--max-requests")] = 100,
    delay_ms: Annotated[int, typer.Option("--delay-ms")] = 250,
    include: Annotated[list[str] | None, typer.Option("--include")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
) -> None:
    """Build inventory from a target/profile or an existing session."""
    if session_id is None and (target_name is None or profile_name is None):
        handle_cli_error(
            InputValidationError("Provide either --session or both --target and --profile.")
        )
    if session_id is not None and (target_name is not None or profile_name is not None):
        handle_cli_error(
            InputValidationError("Use either --session or --target/--profile, not both.")
        )
    controls = InventoryBuildControls(
        max_pages=max_pages,
        max_depth=max_depth,
        max_requests=max_requests,
        delay_ms=delay_ms,
        include=include or [],
        exclude=exclude or [],
    )
    try:
        with session_scope() as session:
            if session_id is not None:
                typer.echo(f"Building inventory from session {session_id}...")
                summary = service.build_from_session(session, session_id, controls)
            else:
                typer.echo(f"Building inventory from target '{target_name}'...")
                typer.echo(f"Using profile '{profile_name}'...")
                summary = service.build_from_target_profile(
                    session,
                    target_name or "",
                    profile_name or "",
                    controls,
                )
        print_inventory_summary(summary)
    except GardenError as error:
        handle_cli_error(error)


@app.command("list")
def list_inventory() -> None:
    """List inventory runs."""
    with session_scope() as session:
        inventory_runs = service.list(session)
        if not inventory_runs:
            typer.echo("No inventory runs found.")
            return
        typer.echo(
            f"{'ID':<4} {'TARGET':<24} {'PROFILE':<24} {'SESSION':<8} "
            f"{'JOB':<6} {'STATUS':<10} COUNTS"
        )
        for inventory_run in inventory_runs:
            target_name = target_service.get(session, inventory_run.target_id).name
            profile_name = credential_service.get(session, inventory_run.credential_profile_id).name
            counts = (
                f"p={inventory_run.pages_count}/"
                f"e={inventory_run.endpoints_count}/"
                f"pa={inventory_run.parameters_count}/"
                f"a={inventory_run.annotations_count}"
            )
            typer.echo(
                f"{inventory_run.id:<4} {target_name[:24]:<24} {profile_name[:24]:<24} "
                f"{inventory_run.auth_session_id:<8} {inventory_run.scan_job_id:<6} "
                f"{inventory_run.status:<10} {counts}"
            )


@app.command("show")
def show_inventory(inventory_run_id: int) -> None:
    """Show inventory run details."""
    try:
        with session_scope() as session:
            inventory_run = service.get(session, inventory_run_id)
            target_name = target_service.get(session, inventory_run.target_id).name
            profile_name = credential_service.get(session, inventory_run.credential_profile_id).name
        typer.echo(f"Inventory Run #{inventory_run.id}")
        typer.echo(f"Target: {target_name}")
        typer.echo(f"Profile: {profile_name}")
        typer.echo(f"Session: {inventory_run.auth_session_id}")
        typer.echo(f"Scan Job: {inventory_run.scan_job_id}")
        typer.echo(f"Status: {inventory_run.status}")
        typer.echo(f"Started From: {inventory_run.started_from_url}")
        typer.echo(
            "Controls: "
            f"max_pages={inventory_run.max_pages} "
            f"max_depth={inventory_run.max_depth} "
            f"max_requests={inventory_run.max_requests} "
            f"delay_ms={inventory_run.delay_ms}"
        )
        typer.echo(
            "Counts: "
            f"pages={inventory_run.pages_count} "
            f"endpoints={inventory_run.endpoints_count} "
            f"parameters={inventory_run.parameters_count} "
            f"annotations={inventory_run.annotations_count}"
        )
        typer.echo(f"Summary: {inventory_run.summary or '-'}")
        typer.echo("Pages:")
        for page in inventory_run.pages[:5]:
            typer.echo(f"  - depth={page.depth} visits={page.visit_count} {page.url}")
        typer.echo("Endpoints:")
        for endpoint in inventory_run.endpoints[:5]:
            typer.echo(
                "  - "
                f"{endpoint.method} {endpoint.path} "
                f"status={','.join(str(code) for code in endpoint.status_codes_observed)} "
                f"count={endpoint.request_count}"
            )
        typer.echo("Parameters:")
        for parameter in inventory_run.parameters[:8]:
            typer.echo(
                f"  - {parameter.source_type}:{parameter.name} "
                f"sensitive={'yes' if parameter.sensitive else 'no'} "
                f"count={parameter.seen_count}"
            )
        typer.echo("Annotations:")
        for annotation in inventory_run.annotations[:8]:
            typer.echo(
                f"  - {annotation.subject_type}:{annotation.subject_ref} -> {annotation.marker}"
            )
    except GardenError as error:
        handle_cli_error(error)


@app.command("export")
def export_inventory(
    inventory_run_id: Annotated[int, typer.Option("--id")],
    export_format: Annotated[str, typer.Option("--format")] = "json",
) -> None:
    """Export an inventory run as JSON or CSV."""
    if export_format not in {"json", "csv"}:
        handle_cli_error(InputValidationError("Inventory export format must be 'json' or 'csv'."))
    try:
        with session_scope() as session:
            if export_format == "json":
                result = service.export_json(session, inventory_run_id)
            else:
                result = service.export_csv(session, inventory_run_id)
        typer.echo(f"Exported inventory run {inventory_run_id} to {result.output_path}")
    except GardenError as error:
        handle_cli_error(error)
