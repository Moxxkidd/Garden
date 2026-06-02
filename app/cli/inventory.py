"""Inventory CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import (
    console,
    handle_cli_error,
    print_inventory_summary,
    progress_spinner,
    render_key_value,
    render_table,
    styled_status,
)
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

_SHOW_PAGE_LIMIT = 5
_SHOW_ENDPOINT_LIMIT = 5
_SHOW_PARAM_LIMIT = 8
_SHOW_ANNOTATION_LIMIT = 8


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
            with progress_spinner("Building authenticated inventory ..."):
                if session_id is not None:
                    summary = service.build_from_session(session, session_id, controls)
                else:
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
            console.print("[dim]No inventory runs found.[/dim]")
            return

        headers = ["ID", "Target", "Profile", "Session", "Job", "Status", "Counts"]
        rows = []
        for inv in inventory_runs:
            target_name = target_service.get(session, inv.target_id).name
            profile_name = credential_service.get(session, inv.credential_profile_id).name
            counts = (
                f"p={inv.pages_count}/e={inv.endpoints_count}/"
                f"pa={inv.parameters_count}/a={inv.annotations_count}"
            )
            rows.append([
                str(inv.id),
                target_name,
                profile_name,
                str(inv.auth_session_id),
                str(inv.scan_job_id),
                styled_status(inv.status),
                counts,
            ])
        render_table(headers, rows, title="Inventory Runs")


@app.command("show")
def show_inventory(inventory_run_id: int) -> None:
    """Show inventory run details."""
    try:
        with session_scope() as session:
            inventory_run = service.get(session, inventory_run_id)
            target_name = target_service.get(session, inventory_run.target_id).name
            profile_name = credential_service.get(session, inventory_run.credential_profile_id).name

        rows: list[tuple[str, str]] = [
            ("Target", target_name),
            ("Profile", profile_name),
            ("Session", str(inventory_run.auth_session_id)),
            ("Scan Job", str(inventory_run.scan_job_id)),
            ("Status", styled_status(inventory_run.status)),
            ("Started From", inventory_run.started_from_url),
            (
                "Controls",
                f"max_pages={inventory_run.max_pages}  "
                f"max_depth={inventory_run.max_depth}  "
                f"max_requests={inventory_run.max_requests}  "
                f"delay_ms={inventory_run.delay_ms}",
            ),
            (
                "Counts",
                f"pages={inventory_run.pages_count}  "
                f"endpoints={inventory_run.endpoints_count}  "
                f"parameters={inventory_run.parameters_count}  "
                f"annotations={inventory_run.annotations_count}",
            ),
            ("Summary", inventory_run.summary or "-"),
        ]
        render_key_value(rows, title=f"Inventory Run #{inventory_run.id}")

        # --- Pages ---
        pages = inventory_run.pages
        if pages:
            page_headers = ["Depth", "Visits", "URL"]
            page_rows = [
                [str(p.depth), str(p.visit_count), p.url] for p in pages[:_SHOW_PAGE_LIMIT]
            ]
            caption = (
                f"Showing {min(len(pages), _SHOW_PAGE_LIMIT)} of {len(pages)} pages"
                if len(pages) > _SHOW_PAGE_LIMIT
                else None
            )
            render_table(page_headers, page_rows, title="Pages", caption=caption)

        # --- Endpoints ---
        endpoints = inventory_run.endpoints
        if endpoints:
            ep_headers = ["Method", "Path", "Status Codes", "Count"]
            ep_rows = [
                [
                    e.method,
                    e.path,
                    ",".join(str(c) for c in e.status_codes_observed),
                    str(e.request_count),
                ]
                for e in endpoints[:_SHOW_ENDPOINT_LIMIT]
            ]
            caption = (
                f"Showing {min(len(endpoints), _SHOW_ENDPOINT_LIMIT)} of {len(endpoints)} endpoints"
                if len(endpoints) > _SHOW_ENDPOINT_LIMIT
                else None
            )
            render_table(ep_headers, ep_rows, title="Endpoints", caption=caption)

        # --- Parameters ---
        params = inventory_run.parameters
        if params:
            param_headers = ["Source:Name", "Sensitive", "Count"]
            param_rows = [
                [
                    f"{p.source_type}:{p.name}",
                    "[yellow]yes[/yellow]" if p.sensitive else "[dim]no[/dim]",
                    str(p.seen_count),
                ]
                for p in params[:_SHOW_PARAM_LIMIT]
            ]
            caption = (
                f"Showing {min(len(params), _SHOW_PARAM_LIMIT)} of {len(params)} parameters"
                if len(params) > _SHOW_PARAM_LIMIT
                else None
            )
            render_table(param_headers, param_rows, title="Parameters", caption=caption)

        # --- Annotations ---
        annotations = inventory_run.annotations
        if annotations:
            ann_headers = ["Subject", "Marker"]
            ann_rows = [
                [f"{a.subject_type}:{a.subject_ref}", a.marker]
                for a in annotations[:_SHOW_ANNOTATION_LIMIT]
            ]
            caption = (
                f"Showing {min(len(annotations), _SHOW_ANNOTATION_LIMIT)}"
                f" of {len(annotations)} annotations"
                if len(annotations) > _SHOW_ANNOTATION_LIMIT
                else None
            )
            render_table(ann_headers, ann_rows, title="Annotations", caption=caption)

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
        with progress_spinner("Exporting inventory ..."):
            with session_scope() as session:
                if export_format == "json":
                    result = service.export_json(session, inventory_run_id)
                else:
                    result = service.export_csv(session, inventory_run_id)
        console.print(
            f"[green]Exported inventory run {inventory_run_id} to[/green]"
            f" {result.output_path}"
        )
    except GardenError as error:
        handle_cli_error(error)
