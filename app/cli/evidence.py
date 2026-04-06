"""Evidence CLI commands."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from app.cli.utils import format_timestamp, handle_cli_error
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
            typer.echo("No evidence found.")
            return
        typer.echo(
            f"{'ID':<4} {'TYPE':<16} {'FINDING':<8} {'STATUS':<6} {'METHOD':<8} {'URL':<46} TITLE"
        )
        for evidence in evidences:
            status = str(evidence.http_status) if evidence.http_status is not None else "-"
            url = (evidence.url or "-")[:46]
            typer.echo(
                f"{evidence.id:<4} {evidence.evidence_type[:16]:<16} "
                f"{str(evidence.finding_id or '-'): <8} {status:<6} "
                f"{(evidence.http_method or '-')[:8]:<8} {url:<46} {evidence.title}"
            )


@app.command("show")
def show_evidence(evidence_id: int) -> None:
    """Show evidence metadata and redacted payload preview."""
    try:
        with session_scope() as session:
            evidence = service.get(session, evidence_id)
            payload = service.payload_for(evidence)
        typer.echo(f"Evidence #{evidence.id}")
        typer.echo(f"Type: {evidence.evidence_type}")
        typer.echo(f"Title: {evidence.title}")
        typer.echo(f"Summary: {evidence.summary}")
        typer.echo(f"Target ID: {evidence.target_id}")
        typer.echo(f"Credential Profile ID: {evidence.credential_profile_id}")
        typer.echo(f"Session ID: {evidence.session_id}")
        typer.echo(f"Job ID: {evidence.job_id}")
        typer.echo(f"Finding ID: {evidence.finding_id if evidence.finding_id is not None else '-'}")
        typer.echo(
            f"Inventory Run ID: "
            f"{evidence.inventory_run_id if evidence.inventory_run_id is not None else '-'}"
        )
        typer.echo(f"Method: {evidence.http_method or '-'}")
        typer.echo(f"URL: {evidence.url or '-'}")
        typer.echo(f"Page Title: {evidence.page_title or '-'}")
        typer.echo(f"Status: {evidence.http_status if evidence.http_status is not None else '-'}")
        typer.echo(f"Storage Ref: {evidence.storage_ref}")
        typer.echo(f"Created: {format_timestamp(evidence.created_at)}")
        typer.echo("Redacted Preview:")
        typer.echo(evidence.preview_redacted or "-")
        typer.echo("Structured Payload:")
        typer.echo(json.dumps(payload, indent=2))
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
        with session_scope() as session:
            result = service.export_for_finding(
                session,
                finding_id=finding_id,
                export_format=export_format,
            )
        typer.echo(
            f"Exported evidence bundle for finding {result.finding_id} to {result.output_path}"
        )
    except GardenError as error:
        handle_cli_error(error)
