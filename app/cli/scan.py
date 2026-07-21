"""Thin one-URL CLI adapter over the core scan application service."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.utils import console, handle_cli_error, render_key_value
from app.core.errors import GardenError, InputValidationError
from app.schemas.scan import ScanOptions
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService


def scan(
    url: Annotated[str | None, typer.Option("--url", help="Authorized HTTP(S) entry URL.")] = None,
    max_pages: Annotated[int, typer.Option("--max-pages")] = 10,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 2,
    request_timeout_seconds: Annotated[float | None, typer.Option("--request-timeout")] = None,
    overall_timeout_seconds: Annotated[float | None, typer.Option("--overall-timeout")] = None,
    retry_attempts: Annotated[int | None, typer.Option("--retries")] = None,
) -> None:
    """Submit one URL and automatically produce the final structured report."""
    try:
        if url is None:
            url = typer.prompt("Authorized entry URL")
        if not url.strip():
            raise InputValidationError("URL cannot be blank.")
        service = ScanApplicationService(dispatcher=InlineScanDispatcher())
        result = service.start_scan(
            url,
            ScanOptions(
                max_pages=max_pages,
                max_depth=max_depth,
                request_timeout_seconds=request_timeout_seconds,
                overall_timeout_seconds=overall_timeout_seconds,
                retry_attempts=retry_attempts,
            ),
        )
        render_key_value(
            [
                ("Scan", str(result.id)),
                ("Status", result.status),
                ("Progress", f"{result.progress}%"),
                ("Stage", result.current_stage),
                ("Assets", str(result.asset_count)),
                ("Evidence", str(result.evidence_count)),
                ("Findings", str(result.finding_count)),
                ("Retries", str(result.retry_count)),
                ("Report", result.report_path or "not generated"),
            ],
            title="Automatic URL Scan Result",
        )
        if result.failures:
            for failure in result.failures:
                console.print(f"[yellow]{failure.stage}/{failure.code}:[/yellow] {failure.message}")
        if result.status == "failed":
            raise typer.Exit(code=1)
    except GardenError as error:
        handle_cli_error(error)
