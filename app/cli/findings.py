"""Findings CLI commands."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from app.cli.utils import format_timestamp, handle_cli_error
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.services.finding_exports import FindingExportService
from app.services.findings import FindingService

app = typer.Typer(help="Browse explainable low-risk findings.")
service = FindingService()
export_service = FindingExportService()


@app.command("list")
def list_findings(
    status: Annotated[str | None, typer.Option("--status")] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
) -> None:
    """List findings, optionally filtered by status or category."""
    with session_scope() as session:
        findings = service.list(session, status=status, category=category)
        if not findings:
            typer.echo("No findings found.")
            return
        typer.echo(f"{'ID':<4} {'SEV':<8} {'CONF':<8} {'STATUS':<10} {'CATEGORY':<30} TITLE")
        for finding in findings:
            typer.echo(
                f"{finding.id:<4} {finding.severity:<8} {finding.confidence:<8} "
                f"{finding.status:<10} {finding.category[:30]:<30} {finding.title}"
            )


@app.command("show")
def show_finding(finding_id: int) -> None:
    """Show detailed finding information."""
    try:
        with session_scope() as session:
            finding = service.get(session, finding_id)
        typer.echo(f"Finding #{finding.id}")
        typer.echo(f"Title: {finding.title}")
        typer.echo(f"Check: {finding.check_name}")
        typer.echo(f"Category: {finding.category}")
        typer.echo(f"Severity: {finding.severity}")
        typer.echo(f"Confidence: {finding.confidence}")
        typer.echo(f"Status: {finding.status}")
        typer.echo(f"Target ID: {finding.target_id}")
        typer.echo(f"Credential Profile ID: {finding.credential_profile_id}")
        typer.echo(f"Session ID: {finding.session_id}")
        typer.echo(f"Job ID: {finding.job_id}")
        typer.echo(f"First Seen: {format_timestamp(finding.first_seen)}")
        typer.echo(f"Last Seen: {format_timestamp(finding.last_seen)}")
        latest_retest = max(finding.retests, key=lambda item: item.id) if finding.retests else None
        if latest_retest is not None:
            typer.echo(f"Last Retest: {format_timestamp(latest_retest.created_at)}")
            typer.echo(f"Last Retest Status: {latest_retest.status}")
            typer.echo(f"Last Retest Summary: {latest_retest.summary}")
        typer.echo("Trigger Explanation:")
        typer.echo(finding.trigger_explanation)
        typer.echo("False Positive Boundaries:")
        typer.echo(finding.false_positive_boundaries)
        typer.echo("Inventory References:")
        typer.echo(json.dumps(finding.inventory_refs, indent=2))
        typer.echo("Evidence References:")
        typer.echo(json.dumps(finding.evidence_refs, indent=2))
        typer.echo("Reproduction Notes:")
        typer.echo(finding.reproduction_notes)
        typer.echo("Remediation Notes:")
        typer.echo(finding.remediation_notes)
    except GardenError as error:
        handle_cli_error(error)


@app.command("update-status")
def update_finding_status(
    finding_id: int,
    status: Annotated[str, typer.Option("--status")],
) -> None:
    """Update a finding lifecycle status using the centralized state machine."""
    try:
        with session_scope() as session:
            finding = service.update_status(session, finding_id, status)
        typer.echo(f"Updated finding {finding.id} to status '{finding.status}'.")
    except GardenError as error:
        handle_cli_error(error)


@app.command("export")
def export_findings(
    export_format: Annotated[str, typer.Option("--format")] = "md",
) -> None:
    """Export findings in redacted markdown, JSON, or CSV format."""
    if export_format not in {"md", "json", "csv"}:
        handle_cli_error(
            InputValidationError("Findings export format must be 'md', 'json', or 'csv'.")
        )
    try:
        with session_scope() as session:
            findings = service.list(session)
            breakdown = service.status_breakdown(session)
            if export_format == "md":
                result = export_service.export_markdown(findings, status_breakdown=breakdown)
            elif export_format == "json":
                result = export_service.export_json(findings, status_breakdown=breakdown)
            else:
                result = export_service.export_csv(findings)
        typer.echo(f"Exported findings to {result.output_path}")
    except GardenError as error:
        handle_cli_error(error)
