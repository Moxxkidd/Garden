"""Tests for the baseline snapshot/diff CLI feature."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import app.cli.baseline as baseline_cli
from app.cli.baseline import (
    build_snapshot_document,
    diff_findings,
    finding_key,
    load_snapshot,
    snapshot_findings,
)
from app.cli.main import app

runner = CliRunner()


def _rec(key_check, location, *, severity="medium", status="new", title="t", category="c"):
    return {
        "key": finding_key(key_check, location),
        "check_name": key_check,
        "location_key": location,
        "title": title,
        "category": category,
        "severity": severity,
        "confidence": "medium",
        "status": status,
    }


# ---------------------------------------------------------------------------
# Pure diff engine
# ---------------------------------------------------------------------------


def test_finding_key_handles_missing_location() -> None:
    assert finding_key("debug_leakage", None) == "debug_leakage::general"
    assert finding_key("debug_leakage", "page:/admin") == "debug_leakage::page:/admin"


def test_diff_classifies_new_resolved_persisting() -> None:
    baseline = [_rec("a", "page:/x"), _rec("b", "page:/y")]
    current = [_rec("b", "page:/y"), _rec("c", "page:/z")]

    diff = diff_findings(baseline, current)

    assert [r["key"] for r in diff.new] == ["c::page:/z"]
    assert [r["key"] for r in diff.resolved] == ["a::page:/x"]
    assert [c.record["key"] for c in diff.persisting] == ["b::page:/y"]


def test_diff_flags_status_and_severity_drift() -> None:
    baseline = [_rec("a", "page:/x", severity="low", status="new")]
    current = [_rec("a", "page:/x", severity="high", status="triaged")]

    diff = diff_findings(baseline, current)

    assert len(diff.persisting) == 1
    change = diff.persisting[0]
    assert change.status_changed is True
    assert change.severity_changed is True
    assert change.previous_status == "new"
    assert change.previous_severity == "low"
    assert diff.changed_persisting == [change]


def test_diff_no_drift_when_identical() -> None:
    records = [_rec("a", "page:/x"), _rec("b", "page:/y")]
    diff = diff_findings(records, list(records))
    assert diff.new == []
    assert diff.resolved == []
    assert len(diff.persisting) == 2
    assert diff.changed_persisting == []


def test_diff_empty_sets() -> None:
    diff = diff_findings([], [])
    assert diff.new == []
    assert diff.resolved == []
    assert diff.persisting == []


def test_diff_severity_filter_scopes_both_sides() -> None:
    baseline = [_rec("a", "p1", severity="high"), _rec("b", "p2", severity="low")]
    current = [_rec("a", "p1", severity="high"), _rec("c", "p3", severity="low")]

    diff = diff_findings(baseline, current, severity="high")

    # Only the high-severity findings participate; low ones are filtered out.
    assert [r["key"] for r in diff.new] == []
    assert [r["key"] for r in diff.resolved] == []
    assert [c.record["key"] for c in diff.persisting] == ["a::p1"]


def test_diff_status_filter() -> None:
    baseline = [_rec("a", "p1", status="new"), _rec("b", "p2", status="closed")]
    current = [_rec("a", "p1", status="new")]

    diff = diff_findings(baseline, current, status="new")

    assert [c.record["key"] for c in diff.persisting] == ["a::p1"]
    assert diff.resolved == []  # the closed one is filtered out, not counted resolved


# ---------------------------------------------------------------------------
# Snapshot serialization / IO
# ---------------------------------------------------------------------------


def test_snapshot_findings_builds_keys() -> None:
    findings = [
        SimpleNamespace(
            check_name="debug_leakage",
            location_key="page:/admin",
            title="Debug visible",
            category="debug_error_leakage",
            severity="medium",
            confidence="medium",
            status="new",
        )
    ]
    records = snapshot_findings(findings)
    assert records[0]["key"] == "debug_leakage::page:/admin"
    assert records[0]["check_name"] == "debug_leakage"


def test_load_snapshot_backfills_missing_keys(tmp_path: Path) -> None:
    document = {
        "garden_baseline_version": 1,
        "findings": [
            {"check_name": "x", "location_key": "page:/a", "severity": "low", "status": "new"}
        ],
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    records = load_snapshot(path)
    assert records[0]["key"] == "x::page:/a"


def test_build_snapshot_document_envelope() -> None:
    records = [_rec("a", "p1")]
    document = build_snapshot_document("rel-1", records)
    assert document["name"] == "rel-1"
    assert document["finding_count"] == 1
    assert document["garden_baseline_version"] == 1
    assert "created_at" in document


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_baseline_save_list_diff_file_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    base_doc = build_snapshot_document("base", [_rec("a", "p1"), _rec("b", "p2")])
    curr_doc = build_snapshot_document("curr", [_rec("b", "p2", status="triaged"), _rec("c", "p3")])
    (tmp_path / "base.json").write_text(json.dumps(base_doc), encoding="utf-8")
    (tmp_path / "curr.json").write_text(json.dumps(curr_doc), encoding="utf-8")

    result = runner.invoke(
        app,
        ["baseline", "diff", "--from", "base.json", "--to", "curr.json"],
    )
    assert result.exit_code == 0
    assert "New findings" in result.stdout
    assert "Resolved findings" in result.stdout
    assert "+1 new" in result.stdout
    assert "-1 resolved" in result.stdout


def test_baseline_diff_json_format(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    base_doc = build_snapshot_document("base", [_rec("a", "p1")])
    curr_doc = build_snapshot_document("curr", [_rec("a", "p1"), _rec("c", "p3")])
    (tmp_path / "base.json").write_text(json.dumps(base_doc), encoding="utf-8")
    (tmp_path / "curr.json").write_text(json.dumps(curr_doc), encoding="utf-8")

    result = runner.invoke(
        app,
        ["baseline", "diff", "--from", "base.json", "--to", "curr.json", "--format", "json"],
    )
    assert result.exit_code == 0
    assert '"new": 1' in result.stdout


def test_baseline_diff_fail_on_new_exit_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    base_doc = build_snapshot_document("base", [_rec("a", "p1")])
    curr_doc = build_snapshot_document("curr", [_rec("a", "p1"), _rec("c", "p3")])
    (tmp_path / "base.json").write_text(json.dumps(base_doc), encoding="utf-8")
    (tmp_path / "curr.json").write_text(json.dumps(curr_doc), encoding="utf-8")

    result = runner.invoke(
        app,
        ["baseline", "diff", "--from", "base.json", "--to", "curr.json", "--fail-on-new"],
    )
    assert result.exit_code == 1


def test_baseline_save_and_list_against_live_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    save_result = runner.invoke(app, ["baseline", "save", "empty-base"])
    assert save_result.exit_code == 0
    assert "Saved baseline" in save_result.stdout
    assert (tmp_path / "baselines" / "empty-base.json").exists()

    list_result = runner.invoke(app, ["baseline", "list"])
    assert list_result.exit_code == 0
    assert "empty-base" in list_result.stdout


def test_baseline_save_rejects_bad_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["baseline", "save", "../evil"])
    assert result.exit_code == 1
    assert "path separators" in result.stdout
