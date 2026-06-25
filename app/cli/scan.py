"""One-command terminal workflow for authenticated findings and reports."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlparse, urlunparse

import typer

from app.cli.utils import (
    console,
    handle_cli_error,
    print_check_run_summary,
    print_inventory_summary,
    progress_spinner,
    render_key_value,
    render_table,
    styled_confidence,
    styled_severity,
    styled_status,
)
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.models.enums import AuthType, TargetStatus, TargetType
from app.schemas.credential import CredentialProfileCreate
from app.schemas.inventory import InventoryBuildControls
from app.schemas.target import TargetCreate, normalize_base_url
from app.services.credentials import CredentialProfileService
from app.services.findings import CheckRunService, FindingService
from app.services.inventory import InventoryBuildService
from app.services.login_configs import encode_inline_login_config
from app.services.reporting import ReportService
from app.services.targets import TargetService

PASSWORD_ENV_NAME = "GARDEN_QUICKSCAN_PASSWORD"

target_service = TargetService()
credential_service = CredentialProfileService()
inventory_service = InventoryBuildService()
check_service = CheckRunService()
finding_service = FindingService()
report_service = ReportService()


def scan(
    url: Annotated[str | None, typer.Option("--url", help="Login page or app URL.")] = None,
    username: Annotated[str | None, typer.Option("--username", "-u")] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", "-p", envvar=PASSWORD_ENV_NAME),
    ] = None,
    target_name: Annotated[str | None, typer.Option("--target-name")] = None,
    profile_name: Annotated[str | None, typer.Option("--profile-name")] = None,
    owner: Annotated[str, typer.Option("--owner")] = "quickscan",
    role: Annotated[str, typer.Option("--role")] = "quickscan",
    validate_path: Annotated[str, typer.Option("--validate-path")] = "/",
    username_selector: Annotated[str | None, typer.Option("--username-selector")] = None,
    password_selector: Annotated[str | None, typer.Option("--password-selector")] = None,
    submit_selector: Annotated[str | None, typer.Option("--submit-selector")] = None,
    success_selector: Annotated[str | None, typer.Option("--success-selector")] = None,
    success_text: Annotated[str | None, typer.Option("--success-text")] = None,
    success_url_contains: Annotated[str | None, typer.Option("--success-url-contains")] = None,
    max_pages: Annotated[int, typer.Option("--max-pages")] = 25,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 2,
    max_requests: Annotated[int, typer.Option("--max-requests")] = 100,
    delay_ms: Annotated[int, typer.Option("--delay-ms")] = 250,
    report_format: Annotated[str, typer.Option("--report-format")] = "md",
) -> None:
    """Run login, inventory, checks, findings, and report from terminal inputs only."""
    try:
        url = _prompt_if_missing(url, "URL")
        username = _prompt_if_missing(username, "Username")
        if password is None:
            password = typer.prompt("Password", hide_input=True)
        if not password:
            raise InputValidationError("Password cannot be blank.")
        if report_format != "md":
            raise InputValidationError("Quick scan reports currently support only markdown.")

        target_base_url, login_path = _split_target_and_login_url(url)
        target_name = target_name or _default_target_name(target_base_url)
        profile_name = profile_name or _default_profile_name(username)
        login_config_ref = _build_inline_playwright_config(
            login_path=login_path,
            validate_path=validate_path,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            success_selector=success_selector,
            success_text=success_text,
            success_url_contains=success_url_contains,
        )
        os.environ[PASSWORD_ENV_NAME] = password

        controls = InventoryBuildControls(
            max_pages=max_pages,
            max_depth=max_depth,
            max_requests=max_requests,
            delay_ms=delay_ms,
            include=[],
            exclude=[],
        )

        with session_scope() as session:
            target = _get_or_create_target(
                session,
                name=target_name,
                base_url=target_base_url,
                owner=owner,
            )
            credential = credential_service.create(
                session,
                CredentialProfileCreate(
                    target_id=target.id,
                    name=_unique_profile_name(session, target.id, profile_name),
                    role=role,
                    auth_type=AuthType.PASSWORD,
                    username=username,
                    secret_ref=f"env://{PASSWORD_ENV_NAME}",
                    login_config_path=login_config_ref,
                ),
            )
            render_key_value(
                [
                    ("Target", f"{target.name} ({target.base_url})"),
                    ("Profile", credential.name),
                    ("Login Path", login_path),
                    ("Validate Path", validate_path),
                ],
                title="Quick Scan Inputs",
            )

            with progress_spinner("Running login and building authenticated inventory ..."):
                inventory_summary = inventory_service.build_from_target_profile(
                    session,
                    target.name,
                    credential.name,
                    controls,
                )
            print_inventory_summary(inventory_summary)

            with progress_spinner("Running checks and capturing redacted evidence ..."):
                check_summary = check_service.run_inventory_checks(
                    session,
                    inventory_summary.inventory_run_id,
                )
            print_check_run_summary(check_summary)

            with progress_spinner("Generating markdown report ..."):
                report = report_service.generate(
                    session,
                    job_id=check_summary.scan_job_id,
                    export_format=report_format,
                )
            findings = finding_service.list(session, job_id=check_summary.scan_job_id)

        _print_findings(findings)
        render_key_value(
            [
                ("Inventory Run", str(inventory_summary.inventory_run_id)),
                ("Inventory Job", str(inventory_summary.scan_job_id)),
                ("Checks Job", str(check_summary.scan_job_id)),
                ("Report", report.output_path),
            ],
            title="Quick Scan Result",
        )
    except GardenError as error:
        handle_cli_error(error)


def _prompt_if_missing(value: str | None, label: str) -> str:
    if value is None:
        value = typer.prompt(label)
    cleaned = value.strip()
    if not cleaned:
        raise InputValidationError(f"{label} cannot be blank.")
    return cleaned


def _split_target_and_login_url(raw_url: str) -> tuple[str, str]:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InputValidationError("URL must be a valid http or https URL.")
    target_base_url = normalize_base_url(urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")))
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return target_base_url, path


def _default_target_name(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = re.sub(r"[^a-zA-Z0-9]+", "-", parsed.netloc).strip("-").lower()
    return f"quick-{host or 'target'}"


def _default_profile_name(username: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", username).strip("-").lower()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"quick-{cleaned or 'user'}-{stamp}"


def _build_inline_playwright_config(
    *,
    login_path: str,
    validate_path: str,
    username_selector: str | None,
    password_selector: str | None,
    submit_selector: str | None,
    success_selector: str | None,
    success_text: str | None,
    success_url_contains: str | None,
) -> str:
    config = {
        "adapter": "playwright",
        "request_timeout_seconds": 15,
        "retry_attempts": 1,
        "login_url": login_path,
        "validate_url": validate_path,
        "username_selector": username_selector or "auto",
        "password_selector": password_selector or "auto",
        "submit_selector": submit_selector or "auto",
        "success_selector": success_selector,
        "success_text": success_text,
        "success_url_contains": success_url_contains,
        "refresh_via_relogin": True,
        "auto_detect_selectors": not (
            username_selector and password_selector and submit_selector
        ),
    }
    return encode_inline_login_config({key: value for key, value in config.items() if value})


def _get_or_create_target(session, *, name: str, base_url: str, owner: str):
    for target in target_service.list(session):
        if target.base_url == base_url or target.name == name:
            return target
    return target_service.create(
        session,
        TargetCreate(
            name=name,
            base_url=base_url,
            type=TargetType.WEB,
            owner=owner,
            tags=["quickscan"],
            status=TargetStatus.ACTIVE,
        ),
    )


def _unique_profile_name(session, target_id: int, preferred: str) -> str:
    existing = {
        credential.name
        for credential in credential_service.list(session)
        if credential.target_id == target_id
    }
    if preferred not in existing:
        return preferred
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate = f"{preferred}-{stamp}"
    index = 2
    while candidate in existing:
        candidate = f"{preferred}-{stamp}-{index}"
        index += 1
    return candidate


def _print_findings(findings) -> None:
    if not findings:
        console.print("[dim]No findings were produced by the enabled checks.[/dim]")
        return
    rows = [
        [
            str(finding.id),
            styled_severity(finding.severity),
            styled_confidence(finding.confidence),
            styled_status(finding.status),
            finding.category[:30],
            finding.title,
        ]
        for finding in findings
    ]
    render_table(
        ["ID", "Severity", "Confidence", "Status", "Category", "Title"],
        rows,
        title="Findings",
    )
