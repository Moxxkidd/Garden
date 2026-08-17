"""正式 `garden scan` 命令：通过本机 Web UI 提交并观察扫描。"""

from __future__ import annotations

import time
from typing import Annotated

import typer

from app.cli.local_api import LocalScanApi
from app.cli.paths import GardenPaths
from app.cli.utils import console, handle_cli_error, render_key_value
from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager
from app.core.errors import GardenError, InputValidationError
from app.core.settings import get_settings
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES
from app.schemas.scan import ScanOptions, ScanRunView


def scan(
    entry_url: Annotated[str | None, typer.Argument(help="已授权的 HTTP(S) 入口 URL。")] = None,
    legacy_url: Annotated[str | None, typer.Option("--url", help="兼容旧版 URL 参数。")] = None,
    max_pages: Annotated[int, typer.Option("--max-pages")] = 50,
    max_resources: Annotated[int, typer.Option("--max-resources")] = 200,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 2,
    request_timeout_seconds: Annotated[float | None, typer.Option("--request-timeout")] = None,
    overall_timeout_seconds: Annotated[float | None, typer.Option("--overall-timeout")] = None,
    retry_attempts: Annotated[int | None, typer.Option("--retries")] = None,
    detach: Annotated[bool, typer.Option("--detach", help="提交后立即返回。")] = False,
    ui_port: Annotated[int | None, typer.Option("--ui-port", min=1, max=65535)] = None,
) -> None:
    """扫描一个已授权 URL；默认前台显示进度。"""

    manager = None
    api = None
    result = None
    try:
        url = _resolve_url(entry_url, legacy_url)
        options = ScanOptions(
            max_pages=max_pages,
            max_resources=max_resources,
            max_depth=max_depth,
            request_timeout_seconds=request_timeout_seconds,
            overall_timeout_seconds=overall_timeout_seconds,
            retry_attempts=retry_attempts,
        )
        manager = WebRuntimeManager(
            paths=GardenPaths.from_environment(), default_port=get_settings().api_port
        )
        runtime = manager.ensure(ui_port=ui_port)
        api = LocalScanApi(runtime.base_url)
        result = api.start_scan(url, options)
        _print_locations(runtime.base_url, result.id)
        if detach:
            console.print(f"扫描 {result.id} 已后台提交。")
            return
        result = _wait_for_scan(api, result)
        _print_result(result)
        if result.status == "failed":
            raise typer.Exit(code=1)
    except KeyboardInterrupt:
        if api is not None and result is not None and manager is not None:
            _cancel_from_interrupt(api, result.id, manager)
        raise typer.Exit(code=130) from None
    except (GardenError, WebRuntimeError) as error:
        handle_cli_error(error)


def _resolve_url(entry_url: str | None, legacy_url: str | None) -> str:
    if entry_url is not None and legacy_url is not None:
        raise InputValidationError("请只使用位置 URL 或 --url 之一，不能同时提供。")
    url = entry_url or legacy_url
    if url is None:
        url = typer.prompt("已授权的入口 URL")
    if not url.strip():
        raise InputValidationError("URL cannot be blank.")
    return url


def _wait_for_scan(api: LocalScanApi, initial: ScanRunView) -> ScanRunView:
    result = initial
    shown: tuple[str, int] | None = None
    while result.status not in TERMINAL_SCAN_RUN_STATUSES:
        progress = (result.current_stage, result.progress)
        if progress != shown:
            console.print(f"扫描 {result.id}：{result.current_stage}，{result.progress}%")
            shown = progress
        time.sleep(0.2)
        result = api.get_scan(result.id)
    return result


def _print_locations(base_url: str, scan_run_id: int) -> None:
    console.print(f"Web UI：{base_url}")
    console.print(f"扫描详情：{base_url}/scans/{scan_run_id}")


def _print_result(result: ScanRunView) -> None:
    render_key_value(
        [
            ("扫描", str(result.id)),
            ("状态", result.status),
            ("进度", f"{result.progress}%"),
            ("阶段", result.current_stage),
            ("资产", str(result.asset_count)),
            ("证据", str(result.evidence_count)),
            ("关注项", str(result.finding_count)),
            ("报告", result.report_path or "未生成"),
        ],
        title="Garden 扫描结果",
    )


def _cancel_from_interrupt(api, scan_run_id, manager) -> None:
    try:
        api.cancel_scan(scan_run_id)
    except Exception:  # noqa: BLE001 - Ctrl+C 必须在有限时间内退出
        pass
    if manager.started_by_this_command:
        manager.stop()
    console.print("扫描已中断。")
    raise typer.Exit(code=130)
