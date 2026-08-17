"""正式 scan 命令的兼容参数、前台/后台和中断语义。"""

from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

import app.cli.scan as scan_cli
import app.cli.stop as stop_cli
from app.cli.main import app
from app.schemas.scan import ScanRunView

runner = CliRunner()


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
