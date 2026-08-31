from datetime import datetime, timezone

from click import unstyle
from typer.testing import CliRunner

import app.cli.coverage as coverage_cli
import app.cli.scan as scan_cli
from app.cli.main import app
from app.cli.web_runtime import WebRuntimeError
from app.schemas.assessment import (
    AssessmentRunView,
    CoverageDifferenceView,
    PassiveCoverageStartRequest,
)
from app.schemas.scan import ScanContextView, ScanRunView

runner = CliRunner()


def _assessment_view(*, status="queued", stage="queued", progress=0):
    return AssessmentRunView(
        id=51,
        mode="authenticated_coverage",
        input_url="http://127.0.0.1:8080/",
        normalized_url="http://127.0.0.1:8080/",
        status=status,
        current_stage=stage,
        progress=progress,
        retry_count=0,
        report_path=("exports/scan-reports/assessment-51.md" if status == "completed" else None),
        created_at=datetime.now(timezone.utc),
        completeness="complete" if status == "completed" else "pending",
        active_checks_enabled=False,
        contexts=[
            ScanContextView(
                id=index,
                kind=kind,
                status="completed" if status == "completed" else "pending",
                collection_status="completed" if status == "completed" else "pending",
                completeness="complete" if status == "completed" else "pending",
            )
            for index, kind in enumerate(("anonymous", "user", "admin"), start=1)
        ],
        difference_count=2 if status == "completed" else 0,
    )


def test_coverage_command_invokes_guided_wizard_and_renders_contexts(monkeypatch) -> None:
    calls = []

    class Runtime:
        base_url = "http://127.0.0.1:8000"

    class Manager:
        started_by_this_command = False

        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            assert ui_port is None
            return Runtime()

    class Wizard:
        def __init__(self, *, prompts):
            calls.append("wizard-created")

        def run(self, url):
            calls.append(("wizard-run", url))
            return PassiveCoverageStartRequest(
                url=url,
                target_id=4,
                user_profile_id=12,
                admin_profile_id=13,
            )

    class Api:
        def __init__(self, base_url):
            assert base_url == Runtime.base_url

        def start_assessment(self, request):
            calls.append(("start", request.user_profile_id, request.admin_profile_id))
            return _assessment_view()

        def get_assessment(self, assessment_id):
            assert assessment_id == 51
            return _assessment_view(status="completed", stage="finished", progress=100)

        def list_coverage_differences(self, assessment_id):
            assert assessment_id == 51
            return [
                CoverageDifferenceView(
                    id=1,
                    identity_key="GET:/account",
                    classification="user_only",
                ),
                CoverageDifferenceView(
                    id=2,
                    identity_key="GET:/admin",
                    classification="admin_only",
                ),
            ]

    monkeypatch.setattr(coverage_cli, "WebRuntimeManager", Manager)
    monkeypatch.setattr(coverage_cli, "CoverageSetupWizard", Wizard)
    monkeypatch.setattr(coverage_cli, "LocalScanApi", Api)
    monkeypatch.setattr(coverage_cli, "_is_interactive_terminal", lambda: True, raising=False)

    result = runner.invoke(app, ["coverage", "http://127.0.0.1:8080/"])

    assert result.exit_code == 0
    assert calls == [
        "wizard-created",
        ("wizard-run", "http://127.0.0.1:8080/"),
        ("start", 12, 13),
    ]
    assert "anonymous" in result.stdout
    assert "user" in result.stdout
    assert "admin" in result.stdout
    assert "user_only" in result.stdout
    assert "admin_only" in result.stdout
    assert "exports/scan-reports/assessment-51.md" in result.stdout
    assert "仅被动" in result.stdout


def test_coverage_command_refuses_to_prompt_without_an_interactive_terminal(
    monkeypatch,
) -> None:
    class ExplodingWizard:
        def __init__(self, **kwargs):
            raise AssertionError("non-TTY coverage must not prompt")

    monkeypatch.setattr(coverage_cli, "CoverageSetupWizard", ExplodingWizard)
    monkeypatch.setattr(coverage_cli, "_is_interactive_terminal", lambda: False, raising=False)

    result = runner.invoke(app, ["coverage", "http://127.0.0.1:8080/"])

    assert result.exit_code == 2
    assert "--non-interactive" in result.stdout


def test_coverage_command_cleans_wizard_secrets_when_runtime_fails_before_submit(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Wizard:
        def __init__(self, *, prompts):
            pass

        def run(self, url):
            return PassiveCoverageStartRequest(
                url=url,
                target_id=4,
                user_profile_id=12,
                admin_profile_id=13,
            )

        def cleanup_unsubmitted_secrets(self):
            calls.append("cleanup")

    class Manager:
        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            raise WebRuntimeError("runtime failed")

    monkeypatch.setattr(coverage_cli, "CoverageSetupWizard", Wizard)
    monkeypatch.setattr(coverage_cli, "WebRuntimeManager", Manager)
    monkeypatch.setattr(coverage_cli, "_is_interactive_terminal", lambda: True, raising=False)

    result = runner.invoke(app, ["coverage", "http://127.0.0.1:8080/"])

    assert result.exit_code == 1
    assert calls == ["cleanup"]


def test_noninteractive_coverage_requires_both_profile_ids() -> None:
    result = runner.invoke(
        app,
        ["coverage", "http://127.0.0.1:8080/", "--non-interactive"],
    )

    assert result.exit_code == 2
    assert "--user-profile 和 --admin-profile" in result.stdout
    assert "选择同源 Target" not in result.stdout


def test_noninteractive_coverage_forwards_bounded_options_and_source_run(
    monkeypatch,
) -> None:
    captured = {}

    class Runtime:
        base_url = "http://127.0.0.1:8000"

    class Manager:
        started_by_this_command = False

        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            return Runtime()

    class Api:
        def __init__(self, base_url):
            pass

        def start_assessment(self, request):
            captured["request"] = request
            return _assessment_view()

    monkeypatch.setattr(coverage_cli, "WebRuntimeManager", Manager)
    monkeypatch.setattr(coverage_cli, "LocalScanApi", Api)

    result = runner.invoke(
        app,
        [
            "coverage",
            "http://127.0.0.1:8080/",
            "--non-interactive",
            "--user-profile",
            "12",
            "--admin-profile",
            "13",
            "--source-run",
            "7",
            "--max-pages",
            "9",
            "--max-resources",
            "17",
            "--max-depth",
            "3",
            "--request-timeout",
            "4",
            "--overall-timeout",
            "80",
            "--retries",
            "2",
            "--detach",
        ],
    )

    assert result.exit_code == 0
    request = captured["request"]
    assert request.source_run_id == 7
    assert request.options.model_dump() == {
        "max_pages": 9,
        "max_resources": 17,
        "max_depth": 3,
        "request_timeout_seconds": 4.0,
        "overall_timeout_seconds": 80.0,
        "retry_attempts": 2,
        "max_redirects": 5,
        "user_agent": "Garden-Authorized-Asset-Scanner/0.2",
    }


def test_coverage_help_reuses_quick_retries_option_name() -> None:
    result = runner.invoke(app, ["coverage", "--help"])
    help_text = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--retries" in help_text
    assert "--retry-attempts" not in help_text


def test_waiting_coverage_renders_context_health_changes(monkeypatch, capsys) -> None:
    initial = _assessment_view(status="queued", stage="establish_contexts", progress=18)
    user_ready = _assessment_view(status="running", stage="collect", progress=30)
    user_context = next(context for context in user_ready.contexts if context.kind.value == "user")
    user_context.status = "ready"
    user_context.login_status = "succeeded"
    user_context.session_validation_status = "valid"
    completed = _assessment_view(status="completed", stage="finished", progress=100)

    class Api:
        def __init__(self) -> None:
            self.results = [user_ready, completed]

        def get_assessment(self, assessment_id):
            return self.results.pop(0)

    monkeypatch.setattr(coverage_cli.time, "sleep", lambda _seconds: None)

    result = coverage_cli._wait_for_assessment(Api(), initial)

    output = capsys.readouterr().out
    assert result.status == "completed"
    assert "登录=succeeded" in output
    assert "验证=valid" in output
    assert "采集=pending" in output


def test_quick_scan_never_invokes_coverage_wizard(monkeypatch) -> None:
    class ExplodingWizard:
        def __init__(self, **kwargs):
            raise AssertionError("quick scan must not create the coverage wizard")

    class Runtime:
        base_url = "http://127.0.0.1:8000"

    class Manager:
        started_by_this_command = False

        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            return Runtime()

    class Api:
        def __init__(self, base_url):
            pass

        def start_scan(self, url, options):
            return ScanRunView(
                id=77,
                input_url=url,
                normalized_url=url,
                status="queued",
                current_stage="queued",
                progress=0,
                retry_count=0,
                created_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr(coverage_cli, "CoverageSetupWizard", ExplodingWizard)
    monkeypatch.setattr(scan_cli, "WebRuntimeManager", Manager)
    monkeypatch.setattr(scan_cli, "LocalScanApi", Api)

    result = runner.invoke(
        app,
        ["scan", "http://127.0.0.1:8080/", "--detach"],
    )

    assert result.exit_code == 0
    assert "已后台提交" in result.stdout


def test_terminal_secret_prompt_uses_hidden_input(monkeypatch) -> None:
    captured = {}

    def prompt(label, **kwargs):
        captured.update({"label": label, **kwargs})
        return "hidden-value"

    monkeypatch.setattr(coverage_cli.typer, "prompt", prompt)

    value = coverage_cli.TyperCoveragePrompts().secret("user 密码（输入已隐藏）")

    assert value == "hidden-value"
    assert captured == {"label": "user 密码（输入已隐藏）", "hide_input": True}
