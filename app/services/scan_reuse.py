"""Read-only eligibility checks shared by preview and task creation."""

from app.core.errors import InputValidationError, ResourceNotFoundError
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun


def reuse_block_reason(run: ScanRun) -> str | None:
    if run.mode != "quick" or run.active_checks_enabled:
        return "此入口仅沿用被动匿名任务；认证任务请使用 coverage 流程。"
    if run.status not in TERMINAL_SCAN_RUN_STATUSES:
        return "来源任务尚未结束，请等待结束后再沿用配置。"
    return None


def reusable_run(session, run_id: int) -> ScanRun:
    run = session.get(ScanRun, run_id)
    if run is None:
        raise ResourceNotFoundError("来源任务不存在，请返回扫描列表。")
    reason = reuse_block_reason(run)
    if reason:
        raise InputValidationError(reason)
    return run
