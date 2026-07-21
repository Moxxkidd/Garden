"""Run a real TCP URL-to-report smoke test against the bundled local fixture."""

from __future__ import annotations

import json
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn


class QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return None


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    fixture_root = workspace / "tests" / "fixtures" / "url_scan_site"
    fixture = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(QuietFixtureHandler, directory=str(fixture_root)),
    )
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()

    os.environ.setdefault(
        "GARDEN_DATABASE_URL",
        "sqlite+pysqlite:////private/tmp/garden-url-scan-e2e.db",
    )
    os.environ["GARDEN_ALLOW_NON_LOCAL_TARGETS"] = "false"
    os.environ["GARDEN_ALLOW_PRIVATE_TARGETS"] = "false"
    from app.core.settings import clear_settings_cache
    from app.db.bootstrap import reset_database_state
    from app.main import create_app

    clear_settings_cache()
    reset_database_state()
    api_port = int(os.environ.get("GARDEN_E2E_API_PORT", "8013"))
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=api_port, log_level="warning")
    )
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Garden API did not start within 10 seconds.")

    try:
        with httpx.Client(timeout=5) as client:
            response = client.post(
                f"http://127.0.0.1:{api_port}/api/scans",
                json={
                    "url": f"http://127.0.0.1:{fixture.server_port}/",
                    "options": {"max_pages": 5, "max_depth": 1, "retry_attempts": 0},
                },
            )
            response.raise_for_status()
            initial = response.json()
            scan_id = initial["id"]
            deadline = time.monotonic() + 15
            current = initial
            while current["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.05)
                status_response = client.get(f"http://127.0.0.1:{api_port}/api/scans/{scan_id}")
                status_response.raise_for_status()
                current = status_response.json()
            report_response = client.get(f"http://127.0.0.1:{api_port}/api/scans/{scan_id}/report")
            report_response.raise_for_status()
            detail_response = client.get(f"http://127.0.0.1:{api_port}/scans/{scan_id}")
            detail_response.raise_for_status()
        report = report_response.text
        required_sections = [
            "执行摘要",
            "扫描范围",
            "发现的资产",
            "关键属性",
            "证据",
            "风险或关注项",
            "扫描覆盖范围",
            "失败或未覆盖部分",
            "生成时间",
        ]
        result = {
            "scan_id": scan_id,
            "status": current["status"],
            "progress": current["progress"],
            "stage_statuses": {stage["name"]: stage["status"] for stage in current["stages"]},
            "asset_count": current["asset_count"],
            "evidence_count": current["evidence_count"],
            "finding_count": current["finding_count"],
            "failure_codes": [failure["code"] for failure in current["failures"]],
            "report_path": current["report_path"],
            "required_sections_present": all(
                f"## {section}" in report for section in required_sections
            ),
            "fixture_assets_present": all(
                text in report for text in ["Garden Local Fixture", "Garden Fixture Asset Detail"]
            ),
            "web_detail_contains_report": "Garden Local Fixture" in detail_response.text,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if current["status"] not in {"completed", "completed_with_warnings"}:
            return 1
        if not result["required_sections_present"] or not result["fixture_assets_present"]:
            return 1
        return 0
    finally:
        server.should_exit = True
        api_thread.join(timeout=10)
        fixture.shutdown()
        fixture.server_close()
        fixture_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
