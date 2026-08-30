"""Guided passive authenticated coverage command."""

from __future__ import annotations

import sys
import time
from collections import Counter
from typing import Annotated

import typer

from app.cli.coverage_wizard import CoverageSetupWizard, PromptChoice
from app.cli.local_api import LocalScanApi
from app.cli.paths import GardenPaths
from app.cli.utils import console, handle_cli_error, render_key_value
from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager
from app.core.errors import GardenError
from app.core.settings import get_settings
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES
from app.schemas.assessment import AssessmentRunView, PassiveCoverageStartRequest
from app.schemas.scan import ScanOptions


class TyperCoveragePrompts:
    """Small prompt adapter that keeps all sensitive input hidden."""

    def choose(self, label: str, choices: list[PromptChoice]) -> str:
        console.print(f"\n[bold]{label}[/bold]")
        for index, choice in enumerate(choices, start=1):
            console.print(f"  {index}. {choice.label}", markup=False)
        while True:
            selected = typer.prompt("请输入序号", type=int, default=1)
            if 1 <= selected <= len(choices):
                return choices[selected - 1].value
            console.print(f"请输入 1 到 {len(choices)} 之间的序号。")

    def confirm(self, label: str, *, default: bool = True) -> bool:
        return typer.confirm(label, default=default)

    def text(self, label: str, *, default: str | None = None) -> str:
        if default is None:
            return typer.prompt(label)
        return typer.prompt(label, default=default)

    def secret(self, label: str) -> str:
        return typer.prompt(label, hide_input=True)

    def write(self, text: str) -> None:
        console.print(text, markup=False)


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty()


def coverage(
    entry_url: Annotated[str, typer.Argument(help="已授权的 HTTP(S) 入口 URL。")],
    user_profile: Annotated[int | None, typer.Option("--user-profile", min=1)] = None,
    admin_profile: Annotated[int | None, typer.Option("--admin-profile", min=1)] = None,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", help="跳过向导；必须显式提供两个档案 ID。"),
    ] = False,
    detach: Annotated[bool, typer.Option("--detach", help="提交后立即返回。")] = False,
    ui_port: Annotated[int | None, typer.Option("--ui-port", min=1, max=65535)] = None,
    source_run: Annotated[int | None, typer.Option("--source-run", min=1)] = None,
    max_pages: Annotated[int, typer.Option("--max-pages", min=1, max=500)] = 50,
    max_resources: Annotated[
        int,
        typer.Option("--max-resources", min=0, max=2000),
    ] = 200,
    max_depth: Annotated[int, typer.Option("--max-depth", min=0, max=5)] = 2,
    request_timeout: Annotated[
        float | None,
        typer.Option("--request-timeout", min=0.1, max=60),
    ] = None,
    overall_timeout: Annotated[
        float | None,
        typer.Option("--overall-timeout", min=0.1, max=1800),
    ] = None,
    retry_attempts: Annotated[
        int | None,
        typer.Option("--retries", min=0, max=2),
    ] = None,
) -> None:
    """引导并执行 anonymous/user/admin 三上下文的仅被动覆盖评估。"""

    api = None
    result = None
    manager = None
    wizard = None
    submission_started = False
    try:
        options = ScanOptions(
            max_pages=max_pages,
            max_resources=max_resources,
            max_depth=max_depth,
            request_timeout_seconds=request_timeout,
            overall_timeout_seconds=overall_timeout,
            retry_attempts=retry_attempts,
        )
        if non_interactive:
            if user_profile is None or admin_profile is None:
                console.print("--non-interactive 必须同时提供 --user-profile 和 --admin-profile。")
                raise typer.Exit(code=2)
            request = PassiveCoverageStartRequest(
                url=entry_url,
                source_run_id=source_run,
                user_profile_id=user_profile,
                admin_profile_id=admin_profile,
                options=options,
            )
        else:
            if not _is_interactive_terminal():
                console.print("当前输入不是交互终端；请使用 --non-interactive 并提供两个档案 ID。")
                raise typer.Exit(code=2)
            wizard = CoverageSetupWizard(prompts=TyperCoveragePrompts())
            request = wizard.run(entry_url)
            request = request.model_copy(update={"source_run_id": source_run, "options": options})

        manager = WebRuntimeManager(
            paths=GardenPaths.from_environment(),
            default_port=get_settings().api_port,
        )
        runtime = manager.ensure(ui_port=ui_port)
        api = LocalScanApi(runtime.base_url)
        submission_started = True
        result = api.start_assessment(request)
        console.print(f"Web UI：{runtime.base_url}")
        console.print(f"认证覆盖状态：{runtime.base_url}/api/assessments/{result.id}")
        console.print("模式：仅被动三上下文覆盖；不执行主动权限重放。")
        if detach:
            console.print(f"认证覆盖评估 {result.id} 已后台提交。")
            return
        result = _wait_for_assessment(api, result)
        differences = api.list_coverage_differences(result.id)
        _print_result(result, differences)
        if result.status == "failed":
            raise typer.Exit(code=1)
    except KeyboardInterrupt:
        if api is not None and result is not None and manager is not None:
            _cancel_from_interrupt(api, result.id, manager)
        if wizard is not None and not submission_started:
            wizard.cleanup_unsubmitted_secrets()
        raise typer.Exit(code=130) from None
    except (GardenError, WebRuntimeError) as error:
        if wizard is not None and not submission_started:
            wizard.cleanup_unsubmitted_secrets()
        handle_cli_error(error)


def _wait_for_assessment(api: LocalScanApi, initial: AssessmentRunView) -> AssessmentRunView:
    result = initial
    shown: tuple[object, ...] | None = None
    while result.status not in TERMINAL_SCAN_RUN_STATUSES:
        context_health = tuple(
            (
                context.kind.value,
                context.status,
                context.login_status,
                context.session_validation_status,
                context.collection_status,
            )
            for context in result.contexts
        )
        comparison_status = next(
            (stage.status for stage in result.stages if stage.name == "compare_coverage"),
            "pending",
        )
        progress = (
            result.current_stage,
            result.progress,
            context_health,
            comparison_status,
        )
        if progress != shown:
            console.print(
                f"认证覆盖 {result.id}：{result.current_stage}，{result.progress}%"
                f"，比较={comparison_status}"
            )
            for kind, status, login, validation, collection in context_health:
                console.print(
                    f"  - {kind}: 状态={status}，登录={login}，"
                    f"验证={validation}，采集={collection}",
                    markup=False,
                )
            shown = progress
        time.sleep(0.2)
        result = api.get_assessment(result.id)
    return result


def _print_result(result, differences) -> None:
    render_key_value(
        [
            ("认证覆盖", str(result.id)),
            ("状态", result.status),
            ("完整性", result.completeness),
            ("报告", result.report_path or "未生成"),
        ],
        title="Garden 认证覆盖结果",
    )
    console.print("三上下文：")
    for context in result.contexts:
        console.print(
            f"- {context.kind.value}: 状态={context.status}，登录={context.login_status}，"
            f"验证={context.session_validation_status}，采集={context.collection_status}",
            markup=False,
        )
    counts = Counter(item.classification for item in differences)
    console.print("覆盖分类：")
    if not counts:
        console.print("- 暂无差异")
    for classification, count in sorted(counts.items()):
        console.print(f"- {classification}: {count}", markup=False)


def _cancel_from_interrupt(api, assessment_id, manager) -> None:
    try:
        api.cancel_assessment(assessment_id)
    except Exception:  # noqa: BLE001 - Ctrl+C 必须在有限时间内退出
        pass
    if manager.started_by_this_command:
        manager.stop()
    console.print("认证覆盖评估已中断。")
    raise typer.Exit(code=130)
