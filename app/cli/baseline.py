"""Baseline snapshot and diff CLI commands.

This is a *presentation-layer* feature: it reads the current findings through
the existing read-only ``FindingService.list`` API and compares them against a
saved JSON snapshot. No core models, services, or schemas are modified.

A baseline snapshot is a point-in-time export of the full findings set. A diff
classifies findings into three buckets relative to a baseline:

* ``new``        -- present now, absent in the baseline
* ``resolved``   -- present in the baseline, absent now
* ``persisting`` -- present in both (optionally with status/severity changes)

The cross-run identity key is ``check_name + "::" + (location_key or "general")``
which mirrors how ``FindingService._build_dedup_key`` identifies the "same"
finding across repeated scans.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

from app.cli.utils import (
    console,
    handle_cli_error,
    progress_spinner,
    render_panel,
    render_table,
    styled_severity,
    styled_status,
)
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.services.findings import FindingService

app = typer.Typer(help="Snapshot findings and diff against a baseline.")
service = FindingService()

BASELINE_VERSION = 1


# ---------------------------------------------------------------------------
# Snapshot helpers (presentation layer — read-only against the ORM)
# ---------------------------------------------------------------------------


def finding_key(check_name: str, location_key: str | None) -> str:
    """Build the stable cross-snapshot identity key for a finding."""
    return f"{check_name}::{location_key or 'general'}"


def snapshot_findings(findings: list[Any]) -> list[dict[str, Any]]:
    """Serialize ORM findings into plain snapshot records with identity keys."""
    records: list[dict[str, Any]] = []
    for finding in findings:
        records.append(
            {
                "key": finding_key(finding.check_name, finding.location_key),
                "check_name": finding.check_name,
                "location_key": finding.location_key,
                "title": finding.title,
                "category": finding.category,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "status": finding.status,
            }
        )
    return records


def default_baseline_path(name: str) -> Path:
    """Return ``baselines/<name>.json`` under the current working directory."""
    safe = name.strip()
    if not safe or "/" in safe or "\\" in safe or safe in {".", ".."}:
        raise InputValidationError(
            "Baseline name must be a simple identifier without path separators."
        )
    baselines_root = Path.cwd() / "baselines"
    baselines_root.mkdir(parents=True, exist_ok=True)
    return baselines_root / f"{safe}.json"


def build_snapshot_document(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap snapshot records in the on-disk baseline document envelope."""
    return {
        "garden_baseline_version": BASELINE_VERSION,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finding_count": len(records),
        "findings": records,
    }


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    """Read a baseline document from disk and return its finding records."""
    if not path.exists():
        raise InputValidationError(f"Baseline file not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InputValidationError(f"Baseline file is not valid JSON: {path}") from error
    findings = document.get("findings") if isinstance(document, dict) else None
    if not isinstance(findings, list):
        raise InputValidationError(f"Baseline file has no findings array: {path}")
    # Backfill identity keys for records that predate the key field.
    for record in findings:
        if "key" not in record:
            record["key"] = finding_key(
                str(record.get("check_name", "")),
                record.get("location_key"),
            )
    return findings


# ---------------------------------------------------------------------------
# Pure diff engine (no DB, no IO — directly unit-testable)
# ---------------------------------------------------------------------------


@dataclass
class PersistingChange:
    """A finding present in both snapshots, with any drift flagged."""

    record: dict[str, Any]
    status_changed: bool
    severity_changed: bool
    previous_status: str | None
    previous_severity: str | None


@dataclass
class BaselineDiff:
    """Result of comparing a baseline snapshot against the current set."""

    new: list[dict[str, Any]] = field(default_factory=list)
    resolved: list[dict[str, Any]] = field(default_factory=list)
    persisting: list[PersistingChange] = field(default_factory=list)

    @property
    def changed_persisting(self) -> list[PersistingChange]:
        return [item for item in self.persisting if item.status_changed or item.severity_changed]


def _matches_filters(
    record: dict[str, Any],
    *,
    severity: str | None,
    status: str | None,
) -> bool:
    if severity and str(record.get("severity", "")).lower() != severity.lower():
        return False
    if status and str(record.get("status", "")).lower() != status.lower():
        return False
    return True


def diff_findings(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    severity: str | None = None,
    status: str | None = None,
) -> BaselineDiff:
    """Compare two snapshot record lists by their identity ``key``.

    Optional ``severity`` / ``status`` filters scope BOTH sides before diffing,
    so the buckets only reflect findings matching the filter.
    """
    base_index = {
        record["key"]: record
        for record in baseline
        if _matches_filters(record, severity=severity, status=status)
    }
    curr_index = {
        record["key"]: record
        for record in current
        if _matches_filters(record, severity=severity, status=status)
    }

    result = BaselineDiff()
    for key, record in curr_index.items():
        if key not in base_index:
            result.new.append(record)
        else:
            base_record = base_index[key]
            prev_status = base_record.get("status")
            prev_severity = base_record.get("severity")
            status_changed = prev_status != record.get("status")
            severity_changed = prev_severity != record.get("severity")
            result.persisting.append(
                PersistingChange(
                    record=record,
                    status_changed=status_changed,
                    severity_changed=severity_changed,
                    previous_status=prev_status,
                    previous_severity=prev_severity,
                )
            )
    for key, record in base_index.items():
        if key not in curr_index:
            result.resolved.append(record)

    result.new.sort(key=lambda r: r["key"])
    result.resolved.sort(key=lambda r: r["key"])
    result.persisting.sort(key=lambda c: c.record["key"])
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_record_table(records: list[dict[str, Any]], *, title: str) -> None:
    headers = ["Severity", "Status", "Category", "Title"]
    rows = [
        [
            styled_severity(str(r.get("severity", "-"))),
            styled_status(str(r.get("status", "-"))),
            str(r.get("category", "-"))[:30],
            str(r.get("title", "-")),
        ]
        for r in records
    ]
    render_table(headers, rows, title=title)


def _render_persisting_table(changes: list[PersistingChange]) -> None:
    headers = ["Severity", "Status", "Drift", "Title"]
    rows = []
    for change in changes:
        drift_parts: list[str] = []
        if change.status_changed:
            drift_parts.append(
                f"status {change.previous_status}→{change.record.get('status')}"
            )
        if change.severity_changed:
            drift_parts.append(
                f"severity {change.previous_severity}→{change.record.get('severity')}"
            )
        drift = "; ".join(drift_parts) if drift_parts else "[dim]-[/dim]"
        rows.append(
            [
                styled_severity(str(change.record.get("severity", "-"))),
                styled_status(str(change.record.get("status", "-"))),
                drift,
                str(change.record.get("title", "-")),
            ]
        )
    render_table(headers, rows, title="Persisting findings")


def render_diff(diff: BaselineDiff, *, baseline_label: str) -> None:
    """Render a baseline diff as Rich tables plus a summary panel."""
    if diff.new:
        _render_record_table(diff.new, title="[red]New findings[/red]")
    if diff.resolved:
        _render_record_table(diff.resolved, title="[green]Resolved findings[/green]")
    if diff.persisting:
        _render_persisting_table(diff.persisting)

    summary = (
        f"Compared against baseline [bold]{baseline_label}[/bold]\n"
        f"[red]+{len(diff.new)} new[/red]   "
        f"[green]-{len(diff.resolved)} resolved[/green]   "
        f"[dim]={len(diff.persisting)} persisting[/dim] "
        f"([yellow]{len(diff.changed_persisting)} drifted[/yellow])"
    )
    render_panel(summary, title="Baseline Diff Summary")


def diff_to_dict(diff: BaselineDiff, *, baseline_label: str) -> dict[str, Any]:
    """Serialize a diff for machine-readable (``--format json``) output."""
    return {
        "baseline": baseline_label,
        "summary": {
            "new": len(diff.new),
            "resolved": len(diff.resolved),
            "persisting": len(diff.persisting),
            "drifted": len(diff.changed_persisting),
        },
        "new": diff.new,
        "resolved": diff.resolved,
        "persisting": [
            {
                **change.record,
                "status_changed": change.status_changed,
                "severity_changed": change.severity_changed,
                "previous_status": change.previous_status,
                "previous_severity": change.previous_severity,
            }
            for change in diff.persisting
        ],
    }


# ---------------------------------------------------------------------------
# Typer commands
# ---------------------------------------------------------------------------


@app.command("save")
def save_baseline(name: str) -> None:
    """Snapshot the current findings set to ``baselines/<name>.json``."""
    try:
        path = default_baseline_path(name)
        with progress_spinner("Saving baseline ..."):
            with session_scope() as session:
                findings = service.list(session)
                records = snapshot_findings(findings)
        document = build_snapshot_document(name, records)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        console.print(
            f"[green]Saved baseline[/green] '{name}' "
            f"({len(records)} findings) to {path}"
        )
    except GardenError as error:
        handle_cli_error(error)


@app.command("list")
def list_baselines() -> None:
    """List saved baselines under the ``baselines/`` directory."""
    baselines_root = Path.cwd() / "baselines"
    if not baselines_root.exists():
        console.print("[dim]No baselines saved yet.[/dim]")
        return
    files = sorted(baselines_root.glob("*.json"))
    if not files:
        console.print("[dim]No baselines saved yet.[/dim]")
        return

    headers = ["Name", "Created", "Findings"]
    rows = []
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows.append([path.stem, "[red]invalid[/red]", "-"])
            continue
        rows.append(
            [
                str(document.get("name", path.stem)),
                str(document.get("created_at", "-")),
                str(document.get("finding_count", len(document.get("findings", [])))),
            ]
        )
    render_table(headers, rows, title="Saved Baselines")


@app.command("diff")
def diff_baseline(
    name: Annotated[str | None, typer.Argument()] = None,
    from_file: Annotated[str | None, typer.Option("--from")] = None,
    to_file: Annotated[str | None, typer.Option("--to")] = None,
    severity: Annotated[str | None, typer.Option("--severity")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    output_format: Annotated[str, typer.Option("--format")] = "table",
    fail_on_new: Annotated[bool, typer.Option("--fail-on-new")] = False,
) -> None:
    """Diff current findings against a baseline (or two snapshot files).

    Provide a baseline NAME to compare the live findings set against a saved
    baseline, or use ``--from A.json --to B.json`` to compare two files.
    """
    try:
        if from_file or to_file:
            if not (from_file and to_file):
                raise InputValidationError("Both --from and --to are required for file diff.")
            baseline_records = load_snapshot(Path(from_file))
            current_records = load_snapshot(Path(to_file))
            baseline_label = Path(from_file).name
        else:
            if not name:
                raise InputValidationError(
                    "Provide a baseline NAME, or use --from/--to with two files."
                )
            baseline_records = load_snapshot(default_baseline_path(name))
            with session_scope() as session:
                current_records = snapshot_findings(service.list(session))
            baseline_label = name

        if output_format not in {"table", "json"}:
            raise InputValidationError("Diff format must be 'table' or 'json'.")

        diff = diff_findings(
            baseline_records,
            current_records,
            severity=severity,
            status=status,
        )

        if output_format == "json":
            console.print_json(
                json.dumps(diff_to_dict(diff, baseline_label=baseline_label))
            )
        else:
            render_diff(diff, baseline_label=baseline_label)

        if fail_on_new and diff.new:
            raise SystemExit(1)
    except GardenError as error:
        handle_cli_error(error)
