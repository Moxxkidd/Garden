"""Explain recorded gaps without inferring absent assets or changing collection."""

from pydantic import ValidationError

from app.schemas.coverage_gap import BudgetGap, CoverageDetails, CoverageGapView
from app.services.coverage_identity import redacted_observed_url
from app.services.scan_result_presentation import diagnostic_hint

_LIMIT_LABELS = {
    "max_pages": "页面数上限",
    "max_resources": "资源数上限",
    "max_depth": "发现深度上限",
}


def capture_budget_gaps(
    uncovered, page_queue, resource_queue, depth_limited, options
) -> dict | None:
    """Partition unique, still unrequested discoveries by their available crawl path."""
    pages = {item.url for item, _depth in page_queue}
    resources = {item.url for item, _depth in resource_queue}
    depth_exclusions = {item.url for item in depth_limited}
    buckets: dict[str, list[str]] = {reason: [] for reason in _LIMIT_LABELS}
    for url in dict.fromkeys(item.url for item in uncovered):
        if url in pages:
            reason = "max_pages"
        elif url in resources:
            reason = "max_resources"
        elif url in depth_exclusions:
            reason = "max_depth"
        else:
            # Future collector paths must not silently turn missing provenance into depth.
            return None
        buckets[reason].append(url)
    items = [
        BudgetGap(
            reason=reason,
            count=len(urls),
            limit=getattr(options, reason),
            samples=[redacted_observed_url(url) for url in urls[:3]],
        )
        for reason, urls in buckets.items()
        if urls
    ]
    return CoverageDetails(items=items).model_dump() if items else None


def explain_coverage_gaps(run) -> list[CoverageGapView]:
    gaps = []
    for failure in run.failures:
        if (failure.stage, failure.code) != ("collect", "coverage_limit_reached"):
            gaps.append(_failure_gap(run, failure))
            continue
        try:
            details = CoverageDetails.model_validate(getattr(failure, "coverage_details", None))
        except (ValidationError, ValueError):
            gaps.append(_failure_gap(run, failure))
            continue
        for item in details.items:
            gaps.append(
                CoverageGapView(
                    reason=item.reason,
                    context="anonymous",
                    count=item.count,
                    samples=_safe_samples(item.samples),
                    summary=(
                        f"匿名：{_LIMIT_LABELS[item.reason]}（{item.limit}）；"
                        f"未请求 URL：{item.count}。"
                    ),
                    next_step=(
                        f"确认授权范围后，检查 --{item.reason.replace('_', '-')}；"
                        "如需调整，请沿用配置修改并重新预览。提交会创建新任务，不是断点续扫。"
                    ),
                )
            )
    if run.mode == "authenticated_coverage":
        by_kind = {context.kind: context for context in run.contexts}
        for kind, label in {"anonymous": "匿名", "user": "普通用户", "admin": "管理员"}.items():
            context = by_kind.get(kind)
            if (
                context
                and context.collection_status == "completed"
                and context.completeness == "complete"
            ):
                continue
            code = context.error_code if context else None
            description = {
                "authentication_session_unavailable": "登录身份不可用，未取得完整采集结果",
                "authentication_session_mismatch": "登录身份与目标或角色不匹配，未取得完整采集结果",
                "context_collection_failed": "身份上下文采集失败",
            }.get(code, "尚无可确认的完整采集结果")
            gaps.append(
                CoverageGapView(
                    reason="context_incomplete",
                    context=kind,
                    summary=(
                        f"{label}：{description}；覆盖影响数量未知，未观察到的内容仍为 unknown。"
                    ),
                    next_step=diagnostic_hint("context", code or ""),
                )
            )
    if not gaps and run.status in {
        "failed",
        "interrupted",
        "incomplete",
        "completed_with_warnings",
    }:
        gaps.append(
            CoverageGapView(
                reason="coverage_unknown",
                summary="任务未完整结束或记录了告警；缺少可用的覆盖缺口明细，覆盖影响数量未知。",
            )
        )
    return gaps


COVERAGE_GAP_NOTE = (
    "仅解释已记录的覆盖缺口；没有条目不代表整个站点已覆盖。数量未知不等于 0，脱敏样例不是完整清单。"
)


def _safe_samples(urls: list[str]) -> list[str]:
    samples = []
    for url in urls[:3]:
        try:
            samples.append(redacted_observed_url(url))
        except ValueError:
            continue
    return samples


def _failure_gap(run, failure) -> CoverageGapView:
    key = (failure.stage, failure.code)
    reason = "stage_failed"
    explanation = "记录了阶段失败；覆盖影响数量未知。"
    if key == ("collect", "coverage_limit_reached"):
        reason = "coverage_limit_reached"
        explanation = "已触及采集限制，但未保存可用的结构化明细；未请求 URL 数量未知。"
    elif key == ("collect", "overall_timeout"):
        reason = "overall_timeout"
        explanation = "采集总时限已到；尚未请求或未完成采集的 URL 数量未知。"
    elif key == ("collect", "cross_origin_redirect_blocked"):
        reason = "scope_boundary"
        explanation = "跳转超出同源边界，目标未继续采集；覆盖影响数量未知。"
    elif failure.stage in {"collect", "validate"} and failure.code in {
        "network_retry_exhausted",
        "transient_http_exhausted",
        "redirect_without_location",
        "redirect_limit_exceeded",
    }:
        reason = "request_failed"
        explanation = "已尝试请求但采集失败；受影响 URL 数量未知，不能据此判断其内容不存在。"
    context = "anonymous" if run.mode == "quick" else None
    label = "匿名" if context else "任务"
    return CoverageGapView(
        reason=reason,
        context=context,
        summary=f"{label}：{explanation}",
        next_step=diagnostic_hint(*key),
    )
