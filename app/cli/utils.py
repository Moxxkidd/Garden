"""CLI rendering and error helpers."""

import json
from datetime import datetime

import typer

from app.core.errors import GardenError
from app.redaction.display import redact_session_metadata
from app.schemas.auth import LoginExecutionResult
from app.schemas.finding import CheckRunSummary
from app.schemas.inventory import InventoryBuildSummary


def handle_cli_error(error: GardenError) -> None:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def format_timestamp(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "-"


def format_tags(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "-"


def print_login_result(result: LoginExecutionResult) -> None:
    typer.echo(f"Target: {result.target_name}")
    typer.echo(f"Profile: {result.profile_name}")
    typer.echo(f"Auth Mode: {result.adapter}")
    typer.echo(f"Status: {result.status.value}")
    typer.echo(f"Success: {'yes' if result.success else 'no'}")
    typer.echo(f"Message: {result.message}")
    if result.failure_reason:
        typer.echo(f"Failure Reason: {result.failure_reason.value}")
    if result.expires_at:
        typer.echo(f"Expires: {result.expires_at.isoformat()}")
    typer.echo(f"Refresh Supported: {'yes' if result.refresh_supported else 'no'}")
    if result.session_metadata_redacted:
        typer.echo("Metadata:")
        typer.echo(json.dumps(redact_session_metadata(result.session_metadata_redacted), indent=2))
    if result.last_error:
        typer.echo(f"Last Error: {result.last_error}")


def print_inventory_summary(summary: InventoryBuildSummary) -> None:
    typer.echo(f"Inventory Run: {summary.inventory_run_id}")
    typer.echo(f"Scan Job: {summary.scan_job_id}")
    typer.echo(f"Target: {summary.target_name}")
    typer.echo(f"Profile: {summary.profile_name}")
    typer.echo(f"Session: {summary.auth_session_id}")
    typer.echo(f"Status: {summary.status}")
    typer.echo(
        "Counts: "
        f"pages={summary.counts.pages} "
        f"endpoints={summary.counts.endpoints} "
        f"parameters={summary.counts.parameters} "
        f"annotations={summary.counts.annotations}"
    )
    typer.echo(f"Summary: {summary.summary}")


def print_check_run_summary(summary: CheckRunSummary) -> None:
    typer.echo(f"Inventory Run: {summary.inventory_run_id}")
    typer.echo(f"Scan Job: {summary.scan_job_id}")
    typer.echo(f"Target: {summary.target_name}")
    typer.echo(f"Profile: {summary.profile_name}")
    typer.echo(f"Session: {summary.session_id}")
    typer.echo(f"Findings Created: {summary.findings_created}")
    typer.echo(f"Findings Updated: {summary.findings_updated}")
    typer.echo(f"Total Findings: {summary.total_findings}")
    typer.echo(f"Summary: {summary.summary}")
