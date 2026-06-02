"""CLI rendering and error helpers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timezone

from rich.box import ROUNDED, SIMPLE
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from app.core.errors import GardenError
from app.redaction.display import redact_session_metadata
from app.schemas.auth import LoginExecutionResult
from app.schemas.finding import CheckRunSummary
from app.schemas.inventory import InventoryBuildSummary

# ---------------------------------------------------------------------------
# Shared console instance
# ---------------------------------------------------------------------------

console = Console()

# ---------------------------------------------------------------------------
# Colour maps for workflow primitives
# ---------------------------------------------------------------------------

SEVERITY_COLOUR: dict[str, str] = {
    "info": "blue",
    "low": "green",
    "medium": "yellow",
    "high": "red",
}

CONFIDENCE_COLOUR: dict[str, str] = {
    "low": "dim",
    "medium": "yellow",
    "high": "green",
}

STATUS_COLOUR: dict[str, str] = {
    "new": "cyan",
    "triaged": "blue",
    "accepted": "yellow",
    "fixed": "green",
    "retest-pending": "magenta",
    "closed": "dim",
    "open": "cyan",
    "hidden": "dim",
}


def severity_style(severity: str) -> str:
    """Return a rich style string for a severity value."""
    colour = SEVERITY_COLOUR.get(severity.lower(), "white")
    return f"bold {colour}"


def confidence_style(confidence: str) -> str:
    """Return a rich style string for a confidence value."""
    colour = CONFIDENCE_COLOUR.get(confidence.lower(), "white")
    return colour


def status_style(status: str) -> str:
    """Return a rich style string for a workflow status value."""
    colour = STATUS_COLOUR.get(status.lower(), "white")
    return colour


def styled_severity(severity: str) -> str:
    """Return a rich-markup string for displaying severity inline."""
    style = severity_style(severity)
    return f"[{style}]{severity.upper()}[/{style}]"


def styled_confidence(confidence: str) -> str:
    """Return a rich-markup string for displaying confidence inline."""
    style = confidence_style(confidence)
    return f"[{style}]{confidence.upper()}[/{style}]"


def styled_status(status: str) -> str:
    """Return a rich-markup string for displaying a status value inline."""
    style = status_style(status)
    return f"[{style}]{status.upper()}[/{style}]"


def http_status_style(code: int) -> str:
    """Return a rich style string for an HTTP status code."""
    if 200 <= code < 300:
        return "green"
    if 300 <= code < 400:
        return "yellow"
    if 400 <= code < 500:
        return "red"
    if 500 <= code < 600:
        return "bold red"
    return "white"


def styled_http_status(code: int | None) -> str:
    """Return a rich-markup string for displaying an HTTP status code."""
    if code is None:
        return "[dim]-[/dim]"
    style = http_status_style(code)
    return f"[{style}]{code}[/{style}]"


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

_HUMAN_CUTOFF_DAYS = 7


def format_timestamp(value: datetime | None) -> str:
    """Return a compact human-friendly time string.

    * ``None``            → ``-``
    * < 60 s ago          → ``just now``
    * < 60 min ago        → ``5m ago``
    * < 24 h ago          → ``3h ago``
    * < 7 days ago        → ``2d ago``
    * otherwise           → ``2026-06-02 14:30``
    """
    if value is None:
        return "-"

    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    diff = now - value
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < _HUMAN_CUTOFF_DAYS:
        return f"{days}d ago"
    return value.strftime("%Y-%m-%d %H:%M")


def format_tags(tags: list[str]) -> str:
    """Comma-join a list of tags, returning ``-`` for empty / None."""
    return ", ".join(tags) if tags else "-"


# ---------------------------------------------------------------------------
# Shared rich renderers
# ---------------------------------------------------------------------------

# A type alias to keep signatures readable.
Row = list[str]


def render_table(
    headers: list[str],
    rows: Sequence[Row],
    *,
    title: str | None = None,
    caption: str | None = None,
    box_type: type = ROUNDED,
) -> None:
    """Print a consistently-styled Rich table.

    Parameters
    ----------
    headers:
        Column headings.
    rows:
        Row data.  Each row must have the same length as *headers*.  Values
        can contain Rich markup (e.g. ``"[red]HIGH[/red]"``).
    title:
        Optional table title shown above the table.
    caption:
        Optional caption shown below the table.
    box_type:
        Rich box style; defaults to ``ROUNDED``.
    """
    table = Table(title=title, caption=caption, box=box_type, expand=False, padding=(0, 1))
    for header in headers:
        table.add_column(header, no_wrap=header.upper() == header, overflow="ellipsis")
    for row in rows:
        table.add_row(*row)
    console.print(table)


def render_key_value(
    items: Sequence[tuple[str, str]],
    *,
    title: str | None = None,
) -> None:
    """Print a two-column key-value table with no header row.

    Parameters
    ----------
    items:
        ``(label, value)`` pairs.  Values may contain Rich markup.
    title:
        Optional table title.
    """
    table = Table(title=title, show_header=False, box=SIMPLE, padding=(0, 1), expand=False)
    table.add_column("Key", style="bold", no_wrap=True)
    table.add_column("Value", overflow="fold")
    for label, value in items:
        table.add_row(label, value)
    console.print(table)


def render_panel(
    text: str,
    *,
    title: str | None = None,
    border_style: str = "",
) -> None:
    """Print a bordered panel for multi-line text blocks."""
    panel = Panel(text.strip(), title=title, border_style=border_style, padding=(0, 1))
    console.print(panel)


def render_json(data: object, *, title: str | None = None) -> None:
    """Print a syntax-highlighted JSON block."""
    if title:
        console.print(f"[bold]{title}[/bold]")
    json_str = json.dumps(data, indent=2, default=str)
    syntax = Syntax(json_str, "json", theme="monokai", background_color="default")
    console.print(syntax)


# ---------------------------------------------------------------------------
# Progress spinner for long-running operations
# ---------------------------------------------------------------------------


@contextmanager
def progress_spinner(message: str):
    """Show a transient spinner while a block executes.

    Usage::

        with progress_spinner("Building inventory ..."):
            result = do_long_work()
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task(description=message, total=None)
        yield


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def handle_cli_error(error: GardenError) -> None:
    console.print(f"[bold red]Error:[/bold red] {error}")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Schema-specific detail printers (delegated to the shared renderers above)
# ---------------------------------------------------------------------------


def print_login_result(result: LoginExecutionResult) -> None:
    rows: list[tuple[str, str]] = [
        ("Target", result.target_name),
        ("Profile", result.profile_name),
        ("Auth Mode", result.adapter),
        ("Status", styled_status(result.status.value)),
        ("Success", "[green]yes[/green]" if result.success else "[red]no[/red]"),
        ("Message", result.message),
    ]
    if result.failure_reason:
        rows.append(("Failure Reason", result.failure_reason.value))
    if result.expires_at:
        rows.append(("Expires", format_timestamp(result.expires_at)))
    rows.append(
        (
            "Refresh Supported",
            "[green]yes[/green]" if result.refresh_supported else "[dim]no[/dim]",
        )
    )
    if result.last_error:
        rows.append(("Last Error", result.last_error))
    render_key_value(rows, title="Login Result")
    if result.session_metadata_redacted:
        render_json(
            redact_session_metadata(result.session_metadata_redacted),
            title="Session Metadata",
        )


def print_inventory_summary(summary: InventoryBuildSummary) -> None:
    counts = summary.counts
    rows: list[tuple[str, str]] = [
        ("Inventory Run", str(summary.inventory_run_id)),
        ("Scan Job", str(summary.scan_job_id)),
        ("Target", summary.target_name),
        ("Profile", summary.profile_name),
        ("Session", str(summary.auth_session_id)),
        ("Status", styled_status(summary.status)),
        (
            "Counts",
            f"pages={counts.pages}  endpoints={counts.endpoints}  "
            f"parameters={counts.parameters}  annotations={counts.annotations}",
        ),
        ("Summary", summary.summary),
    ]
    render_key_value(rows, title="Inventory Build Summary")


def print_check_run_summary(summary: CheckRunSummary) -> None:
    rows: list[tuple[str, str]] = [
        ("Inventory Run", str(summary.inventory_run_id)),
        ("Scan Job", str(summary.scan_job_id)),
        ("Target", summary.target_name),
        ("Profile", summary.profile_name),
        ("Session", str(summary.session_id)),
        ("Findings Created", str(summary.findings_created)),
        ("Findings Updated", str(summary.findings_updated)),
        ("Total Findings", str(summary.total_findings)),
        ("Summary", summary.summary),
    ]
    render_key_value(rows, title="Check Run Summary")
