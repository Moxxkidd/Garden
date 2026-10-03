"""正式 `garden scan` 命令：通过本机 Web UI 提交并观察扫描。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from app.cli.coverage_gaps import print_coverage_gaps
from app.cli.local_api import LocalScanApi
from app.cli.paths import GardenPaths
from app.cli.result_summary import print_result_summary
from app.cli.utils import console, handle_cli_error, render_key_value
from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager
from app.core.errors import GardenError, InputValidationError
from app.core.settings import get_settings
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES
from app.schemas.scan import ScanFailureView, ScanOptions, ScanRunView
from app.services.scan_failure_classification import is_coverage_warning
from app.services.scan_result_presentation import (
    format_execution_progress,
    format_finding_count,
)


def scan(
    entry_url: Annotated[str | None, typer.Argument(help="已授权的 HTTP(S) 入口 URL。")] = None,
    legacy_url: Annotated[str | None, typer.Option("--url", help="兼容旧版 URL 参数。")] = None,
    max_pages: Annotated[int, typer.Option("--max-pages")] = 50,
    max_resources: Annotated[int, typer.Option("--max-resources")] = 200,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 2,
    request_timeout_seconds: Annotated[float | None, typer.Option("--request-timeout")] = None,
    overall_timeout_seconds: Annotated[float | None, typer.Option("--overall-timeout")] = None,
    retry_attempts: Annotated[int | None, typer.Option("--retries")] = None,
    collection_mode: Annotated[str, typer.Option("--collection-mode")] = "http",
    sitemap_enabled: Annotated[bool, typer.Option("--sitemap/--no-sitemap")] = False,
    sitemap_url: Annotated[str, typer.Option("--sitemap-url")] = "",
    js_enabled: Annotated[bool, typer.Option("--js/--no-js")] = False,
    max_candidates: Annotated[int, typer.Option("--max-candidates")] = 2000,
    max_sitemap_documents: Annotated[int, typer.Option("--max-sitemap-documents")] = 10,
    max_sitemap_depth: Annotated[int, typer.Option("--max-sitemap-depth")] = 2,
    max_browser_requests: Annotated[int, typer.Option("--max-browser-requests")] = 300,
    render_wait_ms: Annotated[int, typer.Option("--render-wait-ms")] = 1000,
    seed_format: Annotated[str, typer.Option("--seed-format")] = "urls",
    seed_file: Annotated[
        str | None, typer.Option("--seed-file", help="显式读取 UTF-8 种子文件（最多 1 MiB）。")
    ] = None,
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
            collection_mode=collection_mode,
            sitemap_enabled=sitemap_enabled,
            sitemap_url=sitemap_url,
            js_enabled=js_enabled,
            max_candidates=max_candidates,
            max_sitemap_documents=max_sitemap_documents,
            max_sitemap_depth=max_sitemap_depth,
            max_browser_requests=max_browser_requests,
            render_wait_ms=render_wait_ms,
            seed_format=seed_format,
            seed_input=_read_seed_file(seed_file),
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
    except ValidationError:
        handle_cli_error(InputValidationError("扫描选项无效，请检查模式、格式和预算范围。"))
    except (GardenError, WebRuntimeError) as error:
        handle_cli_error(error)


def _read_seed_file(path: str | None) -> str:
    if path is None:
        return ""
    try:
        with Path(path).open("rb") as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise InputValidationError("种子文件超过 1 MiB 上限。")
        return content.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise InputValidationError("无法读取种子文件，请检查文件权限和 UTF-8 格式。") from None


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
    console.print(f"资产清单：{base_url}/assets?source=scan&run_id={scan_run_id}")


def _print_result(result: ScanRunView) -> None:
    print_result_summary(result)
    render_key_value(
        [
            ("扫描", str(result.id)),
            ("状态", result.status),
            ("执行进度", format_execution_progress(result.progress)),
            ("阶段", result.current_stage),
            ("资产", str(result.asset_count)),
            ("证据", str(result.evidence_count)),
            ("关注项", format_finding_count(result.finding_count, result.finding_group_count)),
            ("报告", result.report_path or "未生成"),
        ],
        title="Garden 扫描结果",
    )
    if result.discovery_summary:
        from app.services.discovery import SOURCE_KINDS

        console.print("发现来源收益（不是站点发现率）：")
        for kind, counts in (result.discovery_summary.get("stats") or {}).items():
            console.print(
                f"{SOURCE_KINDS.get(kind, '来源未知')}：新候选 {counts.get('new_candidates', 0)}，"
                f"响应观察 {counts.get('response_observed', 0)}，"
                f"重复 {counts.get('duplicate_candidates', 0)}"
            )
    print_coverage_gaps(result.coverage_gaps)
    coverage_warnings = [
        failure for failure in result.failures if is_coverage_warning(failure.stage, failure.code)
    ]
    request_failures = [
        failure
        for failure in result.failures
        if not is_coverage_warning(failure.stage, failure.code)
    ]
    _print_diagnostics("覆盖告警", coverage_warnings)
    _print_diagnostics("请求或阶段失败", request_failures)


def _print_diagnostics(title: str, failures: list[ScanFailureView]) -> None:
    if not failures:
        return
    console.print(f"{title}（{len(failures)}）：")
    for failure in failures:
        console.print(f"- [{failure.stage}/{failure.code}]", markup=False)
        console.print(f"  URL={failure.url or '未记录'}")
        console.print(f"  尝试={failure.attempt}")
        console.print(f"  可重试={'是' if failure.retryable else '否'}")
        console.print(f"  说明={failure.message}")


def _cancel_from_interrupt(api, scan_run_id, manager) -> None:
    try:
        api.cancel_scan(scan_run_id)
    except Exception:  # noqa: BLE001 - Ctrl+C 必须在有限时间内退出
        pass
    if manager.started_by_this_command:
        manager.stop()
    console.print("扫描已中断。")
    raise typer.Exit(code=130)
