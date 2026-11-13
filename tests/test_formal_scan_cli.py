"""正式 scan 命令的兼容参数、前台/后台和中断语义。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from click import unstyle
from typer.testing import CliRunner

import app.cli.scan as scan_cli
import app.cli.stop as stop_cli
from app.cli.main import app
from app.schemas.scan import ScanFailureView, ScanRunView

runner = CliRunner()


@pytest.fixture
def run_terminal_scan(monkeypatch):
    runtime = SimpleNamespace(base_url="http://127.0.0.1:8000")
    manager = SimpleNamespace(ensure=lambda **kwargs: runtime)
    monkeypatch.setattr(scan_cli, "WebRuntimeManager", lambda **kwargs: manager)

    def invoke(view):
        api = SimpleNamespace(start_scan=lambda url, options: view)
        monkeypatch.setattr(scan_cli, "LocalScanApi", lambda base_url: api)
        return runner.invoke(app, ["scan", "http://127.0.0.1:3000/"])

    return invoke


@pytest.mark.parametrize(
    ("raw", "groups", "text"),
    [
        (104, 2, "2 类关注项，104 条原始观察"),
        (0, 0, "0 类关注项，0 条原始观察"),
        (104, None, "104 条原始观察（分类数未提供）"),
    ],
)
def test_terminal_scan_shows_grouped_count(run_terminal_scan, raw, groups, text):
    payload = _view(status="completed", stage="finished", progress=100).model_dump()
    payload["finding_count"] = raw
    if groups is None:
        payload.pop("finding_group_count")
    else:
        payload["finding_group_count"] = groups
    result = run_terminal_scan(ScanRunView.model_validate(payload))
    assert result.exit_code == 0
    assert text in unstyle(result.stdout)
    assert "下一步" in unstyle(result.stdout)


@pytest.mark.parametrize(
    ("code", "snippet"),
    [
        ("overall_timeout", "不是断点续扫"),
        ("coverage_limit_reached", "--max-depth"),
        ("cross_origin_redirect_blocked", "应以该目标作为新入口单独扫描"),
    ],
)
def test_terminal_diagnostic_hints_are_fixed_and_deduplicated(run_terminal_scan, code, snippet):
    view = _view(status="completed_with_warnings", stage="finished", progress=100)
    failure = ScanFailureView(
        stage="collect",
        code=code,
        message="token=TEST_SECRET",
        url="http://127.0.0.1/?token=TEST_SECRET",
        retryable=False,
        attempt=1,
        occurred_at=view.created_at,
    )
    view.failures = [failure, failure.model_copy()]
    result = run_terminal_scan(view)
    assert result.exit_code == 0
    text = unstyle(result.stdout)
    assert code in text
    hints = text.split("下一步", 1)[1].split("Garden 扫描结果", 1)[0].replace("\n", "")
    assert hints.count(snippet) == 1
    assert "TEST_SECRET" not in hints


def test_terminal_unknown_diagnostic_keeps_original_without_advice(run_terminal_scan):
    view = _view(status="failed", stage="finished", progress=100)
    view.failures = [
        ScanFailureView(
            stage="report",
            code="unrecognized_failure",
            message="original diagnostic",
            retryable=False,
            attempt=1,
            occurred_at=view.created_at,
        )
    ]
    result = run_terminal_scan(view)
    assert result.exit_code == 1
    assert "original diagnostic" in result.stdout
    assert "查看诊断和执行阶段" in result.stdout
    assert "--retries" not in result.stdout


def _view(*, status="queued", stage="queued", progress=0):
    return ScanRunView(
        id=7,
        input_url="http://127.0.0.1:3000/",
        normalized_url="http://127.0.0.1:3000/",
        status=status,
        current_stage=stage,
        progress=progress,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
    )


def test_position_url_is_foreground_and_detach_returns_immediately(monkeypatch):
    calls = []

    class Runtime:
        base_url = "http://127.0.0.1:8000"

    class Manager:
        started_by_this_command = False

        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            calls.append(("ensure", ui_port))
            return Runtime()

    class Api:
        def __init__(self, base_url):
            assert base_url == Runtime.base_url

        def start_scan(self, url, options):
            calls.append(("start", url, options.max_pages, options.max_resources))
            return _view()

        def get_scan(self, scan_run_id):
            calls.append(("get", scan_run_id))
            return _view(status="completed", stage="finished", progress=100)

    monkeypatch.setattr(scan_cli, "WebRuntimeManager", Manager)
    monkeypatch.setattr(scan_cli, "LocalScanApi", Api)
    foreground = runner.invoke(app, ["scan", "http://127.0.0.1:3000/", "--max-pages", "3"])
    detached = runner.invoke(app, ["scan", "http://127.0.0.1:3000/", "--detach"])

    assert foreground.exit_code == 0
    assert "扫描详情：http://127.0.0.1:8000/scans/7" in foreground.stdout
    assert ("get", 7) in calls
    assert detached.exit_code == 0
    assert "已后台提交" in detached.stdout
    assert calls.count(("get", 7)) == 1
    assert ("start", "http://127.0.0.1:3000/", 50, 200) in calls


def test_position_url_and_legacy_url_cannot_be_mixed():
    result = runner.invoke(
        app,
        ["scan", "http://127.0.0.1:3000/", "--url", "http://127.0.0.1:3001/"],
    )

    assert result.exit_code == 1
    assert "不能同时提供" in result.stdout


def test_ctrl_c_cancels_scan_stops_only_owned_ui_and_exits_130(monkeypatch):
    calls = []

    class Runtime:
        base_url = "http://127.0.0.1:8000"

    class Manager:
        started_by_this_command = True

        def __init__(self, **kwargs):
            pass

        def ensure(self, *, ui_port):
            return Runtime()

        def stop(self):
            calls.append("stop")
            return True

    class Api:
        def __init__(self, base_url):
            pass

        def start_scan(self, url, options):
            return _view()

        def get_scan(self, scan_run_id):
            raise KeyboardInterrupt

        def cancel_scan(self, scan_run_id):
            calls.append(("cancel", scan_run_id))
            return _view(status="interrupted", stage="interrupted")

    monkeypatch.setattr(scan_cli, "WebRuntimeManager", Manager)
    monkeypatch.setattr(scan_cli, "LocalScanApi", Api)
    result = runner.invoke(app, ["scan", "http://127.0.0.1:3000/"])

    assert result.exit_code == 130
    assert calls == [("cancel", 7), "stop"]


def test_stop_quiesces_only_the_authenticated_instance(monkeypatch):
    calls = []
    runtime = SimpleNamespace(base_url="http://127.0.0.1:8123", session_token="synthetic")

    class Manager:
        def __init__(self, **kwargs):
            pass

        def _load_state(self):
            return runtime

        def _is_healthy(self, record):
            return record is runtime

        def stop(self):
            calls.append("stop")
            return True

    class Api:
        def __init__(self, base_url, session_token):
            assert base_url == runtime.base_url and session_token == runtime.session_token

        def _request_json(self, method, path, payload):
            calls.append((method, path, payload))
            return {"idle": True}

    monkeypatch.setattr("app.cli.local_api.LocalScanApi", Api)
    monkeypatch.setattr(stop_cli, "WebRuntimeManager", Manager)
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert calls == [("POST", "/_garden/quiesce", {"cancel": True}), "stop"]
    assert "本机 Garden Web UI 已停止" in result.stdout


def test_legacy_result_with_100_percent_does_not_claim_complete_coverage(run_terminal_scan):
    result = run_terminal_scan(_view(status="completed", stage="finished", progress=100))
    assert result.exit_code == 0
    assert "覆盖完整性未知" in unstyle(result.stdout)
    assert "不代表覆盖完整" in unstyle(result.stdout)


def test_cli_shows_live_counts_even_when_percentage_is_unchanged(monkeypatch, capsys):
    def view(count, status="running"):
        return _view(status=status, stage="collect", progress=20).model_copy(
            update={
                "discovery_summary": {
                    "stats": {
                        "entry": {
                            "request_attempts": count,
                            "response_observed": count,
                            "new_candidates": 5,
                        }
                    },
                    "pending_count": 5 - count,
                }
            }
        )

    states = iter([view(2), view(3, "completed")])
    api = SimpleNamespace(get_scan=lambda _: next(states))
    ticks = iter(range(100))
    monkeypatch.setattr(scan_cli.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(scan_cli.time, "sleep", lambda _: None)
    scan_cli._wait_for_scan(api, view(1))
    output = capsys.readouterr().out
    assert "收到 1 次响应" in output
    assert "收到 2 次响应" in output
    assert "已用时" in output


def test_terminal_scan_explains_explicit_hybrid_gap(run_terminal_scan):
    from app.services.scan_result_presentation import discovery_explanation

    payload = _view(status="completed_with_warnings", stage="finished", progress=100).model_dump()
    payload["discovery_summary"] = {
        "hybrid": {
            "browser_reserved": 3,
            "trigger": "hash_route",
            "browser_status": "unavailable",
            "http_requests": 7,
            "browser_requests": 0,
            "http_observations": 5,
            "browser_observations": 0,
        }
    }
    result = run_terminal_scan(ScanRunView.model_validate(payload))
    assert result.exit_code == 0
    for line in discovery_explanation(payload["discovery_summary"]):
        assert "".join(line.split()) in "".join(result.stdout.split())


def test_cli_preset_survives_real_local_api_transport(app, monkeypatch):
    from app.cli.main import app as cli_app
    from tests.test_scan_preview_e2e import _serve

    received = []
    with _serve(app) as origin:
        service = app.state.scan_service

        def start(url, options):
            received.append(service._resolve_options(options))
            return _view(status="completed", stage="finished", progress=100)

        monkeypatch.setattr(service, "start_scan", start)
        monkeypatch.setattr(
            scan_cli,
            "WebRuntimeManager",
            lambda **kwargs: SimpleNamespace(ensure=lambda **kw: SimpleNamespace(base_url=origin)),
        )
        result = runner.invoke(
            app=cli_app,
            args=[
                "scan",
                "http://localhost:8123/",
                "--preset",
                "deep",
                "--max-requests",
                "50",
                "--no-js",
                "--detach",
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert len(received) == 1
    options = received[0]
    assert options.collection_mode == "hybrid"
    assert options.discovery_strategy == "diversity"
    assert options.sitemap_enabled is True
    assert options.js_enabled is False
    assert options.durable.max_requests == 50
