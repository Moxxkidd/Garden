from datetime import datetime, timezone

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.schemas.scan import ScanRunView
from app.services.scan_reporting import ScanReportService


def _quick_view(url: str) -> ScanRunView:
    return ScanRunView(
        id=31,
        input_url=url,
        normalized_url=url,
        status="queued",
        current_stage="queued",
        progress=0,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
    )


def test_v030_keeps_quick_scan_command_contract() -> None:
    result = CliRunner().invoke(cli_app, ["scan", "--help"])

    assert result.exit_code == 0
    for token in (
        "ENTRY_URL",
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
        assert token in result.stdout
    assert "--user-profile" not in result.stdout
    assert "--admin-profile" not in result.stdout


def test_v030_keeps_quick_http_and_homepage_contract(app) -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.started: tuple[str, object] | None = None

        def start_scan(self, url, options):
            self.started = (url, options)
            return _quick_view(url)

    service = RecordingService()
    with TestClient(app) as client:
        original_service = app.state.scan_service
        app.state.scan_service = service
        try:
            response = client.post(
                "/api/scans",
                json={"url": "https://quick.test/", "options": {"max_pages": 7}},
            )
            homepage = client.get("/")
        finally:
            app.state.scan_service = original_service

    assert response.status_code == 202
    assert response.json()["mode"] == "quick"
    assert service.started is not None
    assert service.started[0] == "https://quick.test/"
    assert service.started[1].max_pages == 7
    assert '<form action="/scans" method="post"' in homepage.text
    assert 'name="url"' in homepage.text


def test_v030_keeps_quick_report_heading_order(tmp_path) -> None:
    with session_scope() as session:
        run = ScanRun(
            mode="quick",
            input_url="https://quick.test/",
            normalized_url="https://quick.test/",
            status="completed",
            current_stage="finished",
            progress=100,
            options={},
        )
        session.add(run)
        session.flush()
        report_path = ScanReportService(output_root=tmp_path).generate(session, run.id)

    text = open(report_path, encoding="utf-8").read()
    headings = [
        "## 执行摘要",
        "## 扫描范围",
        "## 发现的资产",
        "## 关键属性",
        "## 证据",
        "## 已观察到的安全控制",
        "## 风险或关注项",
        "## 扫描覆盖范围",
        "## 失败或未覆盖部分",
        "## 覆盖告警",
        "## 请求失败",
        "## 生成时间",
    ]

    assert text.startswith(f"# Garden 资产报告 #{run.id}\n")
    assert [text.index(heading) for heading in headings] == sorted(
        text.index(heading) for heading in headings
    )
