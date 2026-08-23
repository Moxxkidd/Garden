from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES
from app.schemas.scan import ScanOptions, ScanRunStatus, ScanRunView, ScanStartRequest
from app.services.scan_reporting import ScanReportService

runner = CliRunner()


def test_cli_scan_flags_and_defaults_are_unchanged() -> None:
    result = runner.invoke(cli_app, ["scan", "--help"])

    assert result.exit_code == 0
    for flag in (
        "--url",
        "--max-pages",
        "--max-resources",
        "--max-depth",
        "--request-timeout",
        "--overall-timeout",
        "--retries",
        "--detach",
        "--ui-port",
    ):
        assert flag in result.output
    assert ScanOptions().model_dump() == {
        "max_pages": 50,
        "max_resources": 200,
        "max_depth": 2,
        "request_timeout_seconds": None,
        "overall_timeout_seconds": None,
        "retry_attempts": None,
        "max_redirects": 5,
        "user_agent": "Garden-Authorized-Asset-Scanner/0.2",
    }


def test_scan_api_schema_fields_are_unchanged() -> None:
    assert set(ScanStartRequest.model_fields) == {"url", "options"}
    assert set(ScanRunView.model_fields) == {
        "id",
        "mode",
        "target_id",
        "source_run_id",
        "input_url",
        "normalized_url",
        "status",
        "current_stage",
        "progress",
        "retry_count",
        "report_path",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
        "report_generated_at",
        "created_at",
        "contexts",
        "stages",
        "failures",
        "asset_count",
        "evidence_count",
        "finding_count",
    }


def test_scan_status_semantics_are_unchanged() -> None:
    assert {status.value for status in ScanRunStatus} == {
        "queued",
        "running",
        "completed",
        "completed_with_warnings",
        "failed",
        "incomplete",
        "interrupted",
    }
    assert TERMINAL_SCAN_RUN_STATUSES == {
        "completed",
        "completed_with_warnings",
        "failed",
        "incomplete",
        "interrupted",
    }


def test_homepage_keeps_single_url_submission(app) -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="scan-url"' in response.text
    assert 'name="url"' in response.text
    assert 'type="url"' in response.text
    assert "Start scan" in response.text


def test_report_section_names_and_order_are_unchanged(tmp_path) -> None:
    run = SimpleNamespace(
        id=7,
        normalized_url="http://127.0.0.1/",
        status="completed",
        options={},
        failures=[],
        assets=[],
        evidence=[],
        findings=[],
        stages=[],
    )
    report = "\n".join(
        ScanReportService(tmp_path)._render(run, datetime(2026, 8, 23, tzinfo=timezone.utc))
    )
    headings = [
        "执行摘要",
        "扫描范围",
        "发现的资产",
        "关键属性",
        "证据",
        "已观察到的安全控制",
        "风险或关注项",
        "扫描覆盖范围",
        "失败或未覆盖部分",
        "覆盖告警",
        "请求失败",
        "生成时间",
    ]

    positions = [report.index(f"## {heading}") for heading in headings]
    assert positions == sorted(positions)
