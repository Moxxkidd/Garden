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
        (104, 2, "2 类（104 条原始观察）"),
        (0, 0, "0 类（0 条原始观察）"),
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
    assert "下一步" not in unstyle(result.stdout)


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
    hints = text.split("下一步", 1)[1].replace("\n", "")
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
    assert "下一步" not in result.stdout


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


def test_stop_interrupts_active_scans_and_stops_ui(monkeypatch):
    calls = []

    class Service:
        def __init__(self, **kwargs):
            pass

        def interrupt_active_scans(self):
            calls.append("interrupt")
            return 2

    class Manager:
        def __init__(self, **kwargs):
            pass

        def stop(self):
            calls.append("stop")
            return True

    monkeypatch.setattr(stop_cli, "ScanApplicationService", Service)
    monkeypatch.setattr(stop_cli, "WebRuntimeManager", Manager)
    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert calls == ["interrupt", "stop"]
    assert "已中断 2 个活动扫描" in result.stdout
