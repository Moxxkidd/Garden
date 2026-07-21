from datetime import datetime, timezone

from typer.testing import CliRunner

import app.cli.scan as scan_cli
from app.cli.main import app
from app.schemas.scan import ScanRunView
from app.services.login_configs import LoginConfigService, encode_inline_login_config

runner = CliRunner()


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


def test_scan_command_is_thin_adapter_over_core_service(monkeypatch) -> None:
    calls = []

    class FakeScanService:
        def __init__(self, *, dispatcher):
            assert dispatcher.__class__.__name__ == "InlineScanDispatcher"

        def start_scan(self, url, options):
            calls.append((url, options.max_pages, options.max_depth, options.retry_attempts))
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
            )

    monkeypatch.setattr(scan_cli, "ScanApplicationService", FakeScanService)
    result = runner.invoke(
        app,
        [
            "scan",
            "--url",
            "http://127.0.0.1:8888/",
            "--max-pages",
            "3",
            "--max-depth",
            "1",
            "--retries",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("http://127.0.0.1:8888/", 3, 1, 0)]
    assert "Automatic URL Scan Result" in result.stdout
    assert "exports/scan-reports/scan-41.md" in result.stdout
