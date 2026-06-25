from typer.testing import CliRunner

import app.cli.scan as scan_cli
from app.cli.main import app
from app.schemas.finding import CheckRunSummary
from app.schemas.inventory import InventoryBuildSummary, InventoryCounts
from app.schemas.reporting import JobReportResult
from app.services.login_configs import LoginConfigService, encode_inline_login_config

runner = CliRunner()


def test_inline_playwright_config_round_trip() -> None:
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
    assert config.validate_url == "/"
    assert config.auto_detect_selectors is True
    assert config.username_selector == "auto"


def test_quick_scan_splits_login_url_from_target_origin() -> None:
    base_url, login_path = scan_cli._split_target_and_login_url(
        "http://127.0.0.1:8888/login?next=/dashboard"
    )

    assert base_url == "http://127.0.0.1:8888"
    assert login_path == "/login?next=/dashboard"


def test_scan_command_runs_terminal_workflow_without_config_files(monkeypatch) -> None:
    class FakeInventoryService:
        def __init__(self) -> None:
            self.calls = []

        def build_from_target_profile(self, session, target_name, profile_name, controls):
            self.calls.append((target_name, profile_name, controls.max_pages))
            return InventoryBuildSummary(
                inventory_run_id=11,
                scan_job_id=21,
                target_name=target_name,
                profile_name=profile_name,
                auth_session_id=31,
                status="completed",
                counts=InventoryCounts(pages=2, endpoints=1, parameters=0, annotations=0),
                summary="Collected 2 pages.",
            )

    class FakeCheckService:
        def run_inventory_checks(self, session, inventory_run_id):
            assert inventory_run_id == 11
            return CheckRunSummary(
                inventory_run_id=11,
                scan_job_id=41,
                target_name="quick-127-0-0-1-8888",
                profile_name="quick-user-example-local",
                session_id=31,
                findings_created=0,
                findings_updated=0,
                total_findings=0,
                summary="Checks completed.",
            )

    class FakeReportService:
        def generate(self, session, *, job_id, export_format):
            assert job_id == 41
            assert export_format == "md"
            return JobReportResult(
                job_id=job_id,
                format="md",
                output_path="exports/reports/job-41.md",
            )

    class FakeFindingService:
        def list(self, session, *, job_id=None):
            assert job_id == 41
            return []

    fake_inventory = FakeInventoryService()
    monkeypatch.setattr(scan_cli, "inventory_service", fake_inventory)
    monkeypatch.setattr(scan_cli, "check_service", FakeCheckService())
    monkeypatch.setattr(scan_cli, "report_service", FakeReportService())
    monkeypatch.setattr(scan_cli, "finding_service", FakeFindingService())

    result = runner.invoke(
        app,
        [
            "scan",
            "--url",
            "http://127.0.0.1:8888/login",
            "--username",
            "user@example.local",
            "--password",
            "secret",
            "--profile-name",
            "quick-user-example-local",
            "--max-pages",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert fake_inventory.calls == [("quick-127-0-0-1-8888", "quick-user-example-local", 3)]
    assert "Quick Scan Result" in result.stdout
    assert "exports/reports/job-41.md" in result.stdout
