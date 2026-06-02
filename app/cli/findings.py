"""Findings CLI commands."""

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
    styled_confidence,
    styled_severity,
    styled_status,
)
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
            console.print("[dim]No findings found.[/dim]")
            return

        headers = ["ID", "Severity", "Confidence", "Status", "Category", "Title"]
        rows = [
            [
                str(f.id),
                styled_severity(f.severity),
                styled_confidence(f.confidence),
                styled_status(f.status),
                f.category[:30],
                f.title,
            ]
            for f in findings
        ]
        render_table(headers, rows, title="Findings")


@app.command("show")
def show_finding(finding_id: int) -> None:
    """Show detailed finding information."""
    try:
        with session_scope() as session:
            finding = service.get(session, finding_id)

        rows: list[tuple[str, str]] = [
            ("Title", finding.title),
            ("Check", finding.check_name),
            ("Category", finding.category),
            ("Severity", styled_severity(finding.severity)),
            ("Confidence", styled_confidence(finding.confidence)),
            ("Status", styled_status(finding.status)),
            ("Target ID", str(finding.target_id)),
            ("Credential Profile ID", str(finding.credential_profile_id)),
            ("Session ID", str(finding.session_id)),
            ("Job ID", str(finding.job_id)),
            ("First Seen", format_timestamp(finding.first_seen)),
            ("Last Seen", format_timestamp(finding.last_seen)),
        ]

        latest_retest = max(finding.retests, key=lambda item: item.id) if finding.retests else None
        if latest_retest is not None:
            rows.append(("Last Retest", format_timestamp(latest_retest.created_at)))
            rows.append(("Last Retest Status", styled_status(latest_retest.status)))
            rows.append(("Last Retest Summary", latest_retest.summary))

        render_key_value(rows, title=f"Finding #{finding.id}")

        if finding.trigger_explanation:
            render_panel(finding.trigger_explanation, title="Trigger Explanation")
        if finding.false_positive_boundaries:
            render_panel(
                finding.false_positive_boundaries,
                title="False Positive Boundaries",
                border_style="yellow",
            )
        if finding.inventory_refs:
            render_json(finding.inventory_refs, title="Inventory References")
        if finding.evidence_refs:
            render_json(finding.evidence_refs, title="Evidence References")
        if finding.reproduction_notes:
            render_panel(finding.reproduction_notes, title="Reproduction Notes")
        if finding.remediation_notes:
            render_panel(finding.remediation_notes, title="Remediation Notes", border_style="green")

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
        console.print(f"Updated finding {finding.id} to status {styled_status(finding.status)}.")
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
        with progress_spinner("Exporting findings ..."):
            with session_scope() as session:
                findings = service.list(session)
                breakdown = service.status_breakdown(session)
                if export_format == "md":
                    result = export_service.export_markdown(findings, status_breakdown=breakdown)
                elif export_format == "json":
                    result = export_service.export_json(findings, status_breakdown=breakdown)
                else:
                    result = export_service.export_csv(findings)
        console.print(f"[green]Exported findings to[/green] {result.output_path}")
    except GardenError as error:
        handle_cli_error(error)
