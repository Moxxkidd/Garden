"""Evidence CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import (
    console,
    format_timestamp,
    handle_cli_error,
    progress_spinner,
    render_json,
    render_key_value,
    render_panel,
    render_table,
    styled_http_status,
)
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.services.evidence import EvidenceService

app = typer.Typer(help="Browse and export redacted evidence records.")
service = EvidenceService()


@app.command("list")
def list_evidence(
    finding_id: Annotated[int | None, typer.Option("--finding")] = None,
) -> None:
    """List captured evidence, optionally filtered to one finding."""
    with session_scope() as session:
        evidences = service.list(session, finding_id=finding_id)
        if not evidences:
            console.print("[dim]No evidence found.[/dim]")
            return

        headers = ["ID", "Type", "Finding", "Status", "Method", "URL", "Title"]
        rows = [
            [
                str(e.id),
                e.evidence_type[:16],
                str(e.finding_id or "-"),
                styled_http_status(e.http_status),
                (e.http_method or "-")[:8],
                (e.url or "-"),
                e.title,
            ]
            for e in evidences
        ]
        render_table(headers, rows, title="Evidence")


@app.command("show")
def show_evidence(evidence_id: int) -> None:
    """Show evidence metadata and redacted payload preview."""
    try:
        with session_scope() as session:
            evidence = service.get(session, evidence_id)
            payload = service.payload_for(evidence)

        rows: list[tuple[str, str]] = [
            ("Type", evidence.evidence_type),
            ("Title", evidence.title),
            ("Summary", evidence.summary),
            ("Target ID", str(evidence.target_id)),
            ("Credential Profile ID", str(evidence.credential_profile_id)),
            ("Session ID", str(evidence.session_id)),
            ("Job ID", str(evidence.job_id)),
            ("Finding ID", str(evidence.finding_id) if evidence.finding_id is not None else "-"),
            (
                "Inventory Run ID",
                str(evidence.inventory_run_id) if evidence.inventory_run_id is not None else "-",
            ),
            ("Method", evidence.http_method or "-"),
            ("URL", evidence.url or "-"),
            ("Page Title", evidence.page_title or "-"),
            ("Status", styled_http_status(evidence.http_status)),
            ("Storage Ref", evidence.storage_ref),
            ("Created", format_timestamp(evidence.created_at)),
        ]
        render_key_value(rows, title=f"Evidence #{evidence.id}")

        if evidence.preview_redacted:
            render_panel(evidence.preview_redacted, title="Redacted Preview")
        else:
            console.print("[dim]No redacted preview available.[/dim]")

        render_json(payload, title="Structured Payload")

    except GardenError as error:
        handle_cli_error(error)


@app.command("export")
def export_evidence(
    finding_id: Annotated[int, typer.Option("--finding")],
    export_format: Annotated[str, typer.Option("--format")] = "json",
) -> None:
    """Export redacted evidence for a finding as JSON or Markdown."""
    if export_format not in {"json", "md"}:
        handle_cli_error(InputValidationError("Evidence export format must be 'json' or 'md'."))
    try:
        with progress_spinner("Exporting evidence bundle ..."):
            with session_scope() as session:
                result = service.export_for_finding(
                    session,
                    finding_id=finding_id,
                    export_format=export_format,
                )
        console.print(
            f"[green]Exported evidence bundle for finding {result.finding_id} to[/green] "
            f"{result.output_path}"
        )
    except GardenError as error:
        handle_cli_error(error)
