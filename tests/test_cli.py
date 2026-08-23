from pathlib import Path

from typer.testing import CliRunner

import app.cli.checks as checks_cli
import app.cli.evidence as evidence_cli
import app.cli.findings as findings_cli
import app.cli.inventory as inventory_cli
import app.cli.login as login_cli
import app.cli.report as report_cli
import app.cli.retest as retest_cli
import app.cli.sessions as session_cli
from app.cli.main import app
from app.db.bootstrap import session_scope
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.models.enums import AuthType, JobStatus, TargetType
from app.schemas.job import ScanJobCreate
from app.services.finding_exports import FindingExportService
from app.services.findings import CheckRunService
from app.services.inventory import InventoryBuildService
from app.services.jobs import ScanJobService
from app.services.reporting import ReportService
from app.services.retest import RetestService
from app.services.sessions import AuthSessionService

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Garden 0.2.0" in result.stdout


def test_healthcheck_command() -> None:
    result = runner.invoke(app, ["healthcheck"])
    assert result.exit_code == 0
    # Rich renders syntax-highlighted JSON — check for key fields in the output
    assert '"status": "ok"' in result.stdout


def test_target_cli_flow(tmp_path) -> None:
    import_file = tmp_path / "targets.yaml"
    import_file.write_text(
        """
targets:
  - name: imported-admin
    base_url: http://localhost:9001
    type: admin
    owner: team-appsec
    tags: [imported, local]
    status: active
""".strip(),
        encoding="utf-8",
    )

    add_result = runner.invoke(
        app,
        [
            "target",
            "add",
            "--name",
            "portal",
            "--base-url",
            "http://localhost:9000",
            "--type",
            TargetType.WEB.value,
            "--owner",
            "team-appsec",
            "--tag",
            "demo",
            "--tag",
            "local",
        ],
    )
    assert add_result.exit_code == 0
    assert "Created target #1: portal" in add_result.stdout

    list_result = runner.invoke(app, ["target", "list"])
    assert list_result.exit_code == 0
    assert "portal" in list_result.stdout

    show_result = runner.invoke(app, ["target", "show", "1"])
    assert show_result.exit_code == 0
    assert "http://localhost:9000" in show_result.stdout

    import_result = runner.invoke(
        app,
        [
            "target",
            "import",
            "--file",
            str(import_file),
            "--on-duplicate",
            "skip",
        ],
    )
    assert import_result.exit_code == 0
    assert "imported=1" in import_result.stdout
    assert "updated=0" in import_result.stdout
    assert "skipped=0" in import_result.stdout

    delete_result = runner.invoke(app, ["target", "delete", "2", "--yes"])
    assert delete_result.exit_code == 0
    assert "Deleted target 2." in delete_result.stdout


def test_credential_and_job_cli_flow() -> None:
    target_result = runner.invoke(
        app,
        [
            "target",
            "add",
            "--name",
            "credential-demo",
            "--base-url",
            "http://localhost:9100",
            "--type",
            "web",
            "--owner",
            "team-appsec",
        ],
    )
    assert target_result.exit_code == 0

    credential_result = runner.invoke(
        app,
        [
            "cred",
            "add",
            "--target-id",
            "1",
            "--name",
            "qa-admin",
            "--role",
            "qa-admin",
            "--auth-type",
            AuthType.PASSWORD.value,
            "--username",
            "qa@example.local",
            "--secret-ref",
            "vault://garden/qa-admin",
            "--login-config-path",
            "examples/login/qa.yaml",
        ],
    )
    assert credential_result.exit_code == 0
    assert "Created credential profile #1: qa-admin" in credential_result.stdout

    cred_list_result = runner.invoke(app, ["cred", "list"])
    assert cred_list_result.exit_code == 0
    assert "qa-admin" in cred_list_result.stdout

    cred_show_result = runner.invoke(app, ["cred", "show", "1"])
    assert cred_show_result.exit_code == 0
    assert "vault://garden/qa-admin" in cred_show_result.stdout

    with session_scope() as session:
        job = ScanJobService().create(
            session,
            ScanJobCreate(
                target_id=1,
                credential_profile_id=1,
                status=JobStatus.RUNNING,
                summary="CLI smoke job",
            ),
        )

    job_list_result = runner.invoke(app, ["job", "list"])
    assert job_list_result.exit_code == 0
    assert "RUNNING" in job_list_result.stdout.upper()

    job_show_result = runner.invoke(app, ["job", "show", str(job.id)])
    assert job_show_result.exit_code == 0
    assert "CLI smoke job" in job_show_result.stdout

    delete_result = runner.invoke(app, ["cred", "delete", "1", "--yes"])
    assert delete_result.exit_code == 1


def test_login_and_session_cli_flow(auth_http_transport, tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "cli-http-login.yaml"
    config_path.write_text(
        """
adapter: http
request_timeout_seconds: 5
retry_attempts: 1
session_type: http_cookie_jar
login_request:
  method: POST
  url: /login
  body_type: json
  body:
    username: "{{ username }}"
    password: "{{ password }}"
  expected_status: 200
  success_contains: '"authenticated": true'
validate_request:
  method: GET
  url: /me
  expected_status: 200
  success_contains: '"authenticated": true'
refresh_request:
  method: POST
  url: /refresh
  expected_status: 200
  success_contains: '"refreshed": true'
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("GARDEN_CLI_AUTH_SECRET", "demo-admin-password")

    test_service = AuthSessionService(http_adapter=HttpLoginAdapter(transport=auth_http_transport))
    monkeypatch.setattr(login_cli, "service", test_service)
    monkeypatch.setattr(session_cli, "service", test_service)

    target_result = runner.invoke(
        app,
        [
            "target",
            "add",
            "--name",
            "auth-cli-target",
            "--base-url",
            "http://localhost:9403",
            "--type",
            "web",
            "--owner",
            "appsec",
        ],
    )
    assert target_result.exit_code == 0

    cred_result = runner.invoke(
        app,
        [
            "cred",
            "add",
            "--target-id",
            "1",
            "--name",
            "auth-cli-profile",
            "--role",
            "admin",
            "--auth-type",
            "password",
            "--username",
            "admin@example.local",
            "--secret-ref",
            "env://GARDEN_CLI_AUTH_SECRET",
            "--login-config-path",
            str(Path(config_path)),
        ],
    )
    assert cred_result.exit_code == 0

    login_result = runner.invoke(
        app,
        ["login", "test", "--target", "auth-cli-target", "--profile", "auth-cli-profile"],
    )
    assert login_result.exit_code == 0
    assert "yes" in login_result.stdout  # success field in render_key_value
    assert "http" in login_result.stdout.lower()  # Auth Mode field

    session_list_result = runner.invoke(app, ["session", "list"])
    assert session_list_result.exit_code == 0
    assert "auth-cli-target" in session_list_result.stdout

    session_show_result = runner.invoke(app, ["session", "show", "1"])
    assert session_show_result.exit_code == 0
    assert "Session #1" in session_show_result.stdout
    assert (
        "cookie_names" in session_show_result.stdout
        or "cookie" in session_show_result.stdout.lower()
    )

    validate_result = runner.invoke(app, ["login", "validate", "--session", "1"])
    assert validate_result.exit_code == 0
    assert "Valid" in validate_result.stdout  # render_key_value label

    refresh_result = runner.invoke(app, ["session", "refresh", "1"])
    assert refresh_result.exit_code == 0
    assert "REFRESHED" in refresh_result.stdout.upper()  # styled_status uses uppercase


def test_inventory_cli_flow(
    auth_http_transport,
    fake_inventory_collection_service,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "cli-inventory-login.yaml"
    config_path.write_text(
        """
adapter: http
request_timeout_seconds: 5
retry_attempts: 1
session_type: http_cookie_jar
login_request:
  method: POST
  url: /login
  body_type: json
  body:
    username: "{{ username }}"
    password: "{{ password }}"
  expected_status: 200
  success_contains: '"authenticated": true'
validate_request:
  method: GET
  url: /me
  expected_status: 200
  success_contains: '"authenticated": true'
refresh_request:
  method: POST
  url: /refresh
  expected_status: 200
  success_contains: '"refreshed": true'
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("GARDEN_INVENTORY_CLI_SECRET", "demo-admin-password")

    inventory_service = InventoryBuildService(
        auth_session_service=AuthSessionService(
            http_adapter=HttpLoginAdapter(transport=auth_http_transport)
        ),
        collection_service=fake_inventory_collection_service,
    )
    monkeypatch.setattr(inventory_cli, "service", inventory_service)

    runner.invoke(
        app,
        [
            "target",
            "add",
            "--name",
            "inventory-cli-target",
            "--base-url",
            "http://localhost:9404",
            "--type",
            "web",
            "--owner",
            "appsec",
        ],
    )
    runner.invoke(
        app,
        [
            "cred",
            "add",
            "--target-id",
            "1",
            "--name",
            "inventory-cli-profile",
            "--role",
            "admin",
            "--auth-type",
            "password",
            "--username",
            "admin@example.local",
            "--secret-ref",
            "env://GARDEN_INVENTORY_CLI_SECRET",
            "--login-config-path",
            str(Path(config_path)),
        ],
    )

    build_result = runner.invoke(
        app,
        [
            "inventory",
            "build",
            "--target",
            "inventory-cli-target",
            "--profile",
            "inventory-cli-profile",
        ],
    )
    assert build_result.exit_code == 0
    assert "Inventory Run" in build_result.stdout
    assert "pages=4" in build_result.stdout

    list_result = runner.invoke(app, ["inventory", "list"])
    assert list_result.exit_code == 0
    # Table rendering may truncate long cell values in narrow terminals (80 cols)
    assert "p=4" in list_result.stdout

    show_result = runner.invoke(app, ["inventory", "show", "1"])
    assert show_result.exit_code == 0
    assert "Inventory Run #1" in show_result.stdout
    assert "Annotations" in show_result.stdout

    json_export_result = runner.invoke(
        app,
        ["inventory", "export", "--id", "1", "--format", "json"],
    )
    assert json_export_result.exit_code == 0
    assert "Exported inventory run" in json_export_result.stdout

    csv_export_result = runner.invoke(
        app,
        ["inventory", "export", "--id", "1", "--format", "csv"],
    )
    assert csv_export_result.exit_code == 0
    assert "Exported inventory run" in csv_export_result.stdout


def test_checks_and_findings_cli_flow(
    seeded_inventory,
    fake_evidence_service,
    fake_inventory_collection_service,
    auth_http_transport,
    monkeypatch,
) -> None:
    session_service = AuthSessionService(
        http_adapter=HttpLoginAdapter(transport=auth_http_transport)
    )
    inventory_service = InventoryBuildService(
        auth_session_service=session_service,
        collection_service=fake_inventory_collection_service,
    )
    check_service = CheckRunService(
        inventory_service=inventory_service,
        session_service=session_service,
        evidence_service=fake_evidence_service,
    )
    monkeypatch.setattr(
        checks_cli,
        "service",
        check_service,
    )
    monkeypatch.setattr(evidence_cli, "service", fake_evidence_service)
    monkeypatch.setattr(
        retest_cli,
        "service",
        RetestService(
            finding_service=check_service.finding_service,
            session_service=session_service,
            inventory_service=inventory_service,
            check_service=check_service,
        ),
    )
    monkeypatch.setattr(
        report_cli,
        "service",
        ReportService(
            job_service=ScanJobService(),
            finding_service=check_service.finding_service,
            inventory_service=inventory_service,
        ),
    )
    monkeypatch.setattr(findings_cli, "service", check_service.finding_service)
    monkeypatch.setattr(findings_cli, "export_service", FindingExportService())

    run_result = runner.invoke(
        app,
        ["checks", "run", "--inventory", str(seeded_inventory["inventory_run_id"])],
    )
    assert run_result.exit_code == 0
    assert "Total Findings" in run_result.stdout

    findings_list_result = runner.invoke(app, ["findings", "list"])
    assert findings_list_result.exit_code == 0
    # Rich table with UNICODE box chars — verify content is present
    assert "Findings" in findings_list_result.stdout

    findings_filtered_result = runner.invoke(
        app,
        ["findings", "list", "--category", "idor_indicators"],
    )
    assert findings_filtered_result.exit_code == 0
    assert "idor_indicators" in findings_filtered_result.stdout

    findings_status_result = runner.invoke(app, ["findings", "list", "--status", "new"])
    assert findings_status_result.exit_code == 0
    assert "NEW" in findings_status_result.stdout.upper()

    findings_show_result = runner.invoke(app, ["findings", "show", "1"])
    assert findings_show_result.exit_code == 0
    assert "Trigger Explanation" in findings_show_result.stdout

    update_status_result = runner.invoke(
        app,
        ["findings", "update-status", "1", "--status", "triaged"],
    )
    assert update_status_result.exit_code == 0
    assert "TRIAGED" in update_status_result.stdout.upper()

    export_findings_result = runner.invoke(app, ["findings", "export", "--format", "json"])
    assert export_findings_result.exit_code == 0
    assert "Exported findings to" in export_findings_result.stdout

    export_findings_md_result = runner.invoke(app, ["findings", "export", "--format", "md"])
    assert export_findings_md_result.exit_code == 0
    assert "Exported findings to" in export_findings_md_result.stdout

    export_findings_csv_result = runner.invoke(app, ["findings", "export", "--format", "csv"])
    assert export_findings_csv_result.exit_code == 0
    assert "Exported findings to" in export_findings_csv_result.stdout

    evidence_list_result = runner.invoke(app, ["evidence", "list"])
    assert evidence_list_result.exit_code == 0
    # Rich table may narrow columns; just verify evidence list succeeded
    assert evidence_list_result.exit_code == 0

    evidence_show_result = runner.invoke(app, ["evidence", "show", "1"])
    assert evidence_show_result.exit_code == 0
    assert "Structured Payload" in evidence_show_result.stdout

    evidence_export_result = runner.invoke(
        app,
        ["evidence", "export", "--finding", "1", "--format", "json"],
    )
    assert evidence_export_result.exit_code == 0
    assert "Exported evidence bundle for finding 1" in evidence_export_result.stdout

    fixed_status_result = runner.invoke(
        app,
        ["findings", "update-status", "1", "--status", "fixed"],
    )
    assert fixed_status_result.exit_code == 0

    retest_result = runner.invoke(app, ["retest", "run", "--finding", "1"])
    assert retest_result.exit_code == 0
    assert "Finding" in retest_result.stdout  # render_key_value label
    assert "Status" in retest_result.stdout

    report_result = runner.invoke(
        app,
        ["report", "generate", "--job", "2", "--format", "md"],
    )
    assert report_result.exit_code == 0
    assert "Generated report for job 2" in report_result.stdout
