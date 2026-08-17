from datetime import datetime, timezone

from typer.testing import CliRunner

import app.cli.scan as scan_cli
from app.cli.main import app
from app.schemas.scan import ScanFailureView, ScanOptions, ScanRunView
from app.services.login_configs import LoginConfigService, encode_inline_login_config

runner = CliRunner()


def test_global_version_option_matches_version_command() -> None:
    option = runner.invoke(app, ["--version"])
    command = runner.invoke(app, ["version"])

    assert option.exit_code == 0
    assert option.stdout == command.stdout
    assert "Database initialized" not in command.stderr


def test_inline_playwright_config_round_trip_for_legacy_commands() -> None:
    encoded = encode_inline_login_config(
        {
            "adapter": "playwright",
            "login_url": "/login",
            "validate_url": "/",
            "auto_detect_selectors": True,
        }
    )
    config = LoginConfigService().load(encoded)
    assert config.adapter == "playwright"
    assert config.login_url == "/login"
    assert config.auto_detect_selectors is True


def test_legacy_scan_url_option_submits_through_local_web_ui(monkeypatch) -> None:
    calls = []

    class Runtime:
        base_url = "http://127.0.0.1:8000"

    class FakeRuntimeManager:
        started_by_this_command = False

        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            assert ui_port is None
            return Runtime()

    class FakeLocalScanApi:
        def __init__(self, base_url):
            assert base_url == Runtime.base_url

        def start_scan(self, url, options):
            calls.append(
                (
                    url,
                    options.max_pages,
                    options.max_resources,
                    options.max_depth,
                    options.retry_attempts,
                )
            )
            return ScanRunView(
                id=41,
                input_url=url,
                normalized_url=url,
                status="completed",
                current_stage="finished",
                progress=100,
                retry_count=0,
                report_path="exports/scan-reports/scan-41.md",
                created_at=datetime.now(timezone.utc),
                asset_count=2,
                evidence_count=2,
                finding_count=1,
                failures=[
                    ScanFailureView(
                        stage="collect",
                        code="overall_timeout",
                        message="The overall scan timeout was exceeded.",
                        url="http://127.0.0.1:8888/",
                        retryable=False,
                        attempt=1,
                        occurred_at=datetime.now(timezone.utc),
                    ),
                    ScanFailureView(
                        stage="collect",
                        code="network_retry_exhausted",
                        message="Connection failed.",
                        url="http://127.0.0.1:8888/broken",
                        retryable=True,
                        attempt=2,
                        occurred_at=datetime.now(timezone.utc),
                    ),
                ],
            )

        def get_scan(self, scan_run_id):
            assert scan_run_id == 41
            return self.start_scan("http://127.0.0.1:8888/", ScanOptions())

    monkeypatch.setattr(scan_cli, "WebRuntimeManager", FakeRuntimeManager)
    monkeypatch.setattr(scan_cli, "LocalScanApi", FakeLocalScanApi)
    result = runner.invoke(
        app,
        [
            "scan",
            "--url",
            "http://127.0.0.1:8888/",
            "--max-pages",
            "3",
            "--max-resources",
            "4",
            "--max-depth",
            "1",
            "--retries",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("http://127.0.0.1:8888/", 3, 4, 1, 0)]
    assert "Web UI：http://127.0.0.1:8000" in result.stdout
    assert "Garden 扫描结果" in result.stdout
    assert "exports/scan-reports/scan-41.md" in result.stdout
    assert "覆盖告警" in result.stdout
    assert "[collect/overall_timeout]" in result.stdout
    assert "URL=http://127.0.0.1:8888/" in result.stdout
    assert "尝试=1" in result.stdout
    assert "可重试=否" in result.stdout
    assert "请求或阶段失败" in result.stdout
    assert "[collect/network_retry_exhausted]" in result.stdout
    assert "URL=http://127.0.0.1:8888/broken" in result.stdout
    assert "尝试=2" in result.stdout
    assert "可重试=是" in result.stdout
