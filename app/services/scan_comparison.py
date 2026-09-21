"""Compare stored observations without executing or modifying either scan."""

import math
import re
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun
from app.redaction.service import RedactionService
from app.schemas.scan import ScanOptions
from app.schemas.scan_comparison import (
    ComparisonReason,
    ComparisonRun,
    ConfigurationComparison,
    EvidenceReference,
    ObservationChange,
    ObservationReference,
    ScanComparison,
)
from app.services.coverage_identity import redacted_observed_url
from app.services.scan_result_presentation import coverage_summary

_STATUSES = ("new", "persistent", "not_observed", "unknown")


def _url(value: str) -> str:
    try:
        return redacted_observed_url(value)
    except ValueError:
        return "地址不可解析"


def _text(value: str) -> str:
    value = re.sub(
        r"https?://[^\s<>\"']+", lambda match: _url(match.group()), value, flags=re.IGNORECASE
    )
    return RedactionService().redact_text(value, limit=2000)


def _run_view(run):
    return ComparisonRun(
        id=run.id,
        entry_display=_url(run.normalized_url),
        status=run.status,
        coverage=coverage_summary(run.status, run.completeness, run.mode),
    )


def _precise_location(asset, evidence):
    """Assets are value-redacted and may coalesce several responses: require one exact source."""
    sources = set()
    for item in evidence:
        try:
            parsed = urlsplit(item.source_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                return None
            _ = parsed.port
            if any(
                value.lower() in {"[redacted]", "<redacted>", "***"}
                for _, value in parse_qsl(parsed.query, keep_blank_values=True)
            ):
                return None
            if _url(item.source_url) != _url(asset.url):
                return None
            sources.add(
                urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
            )
        except ValueError:
            return None
    if len(sources) != 1:
        return None
    return asset.method or "GET", next(iter(sources))


class _RunIndex:
    def __init__(self, run):
        self.run = run
        self.assets = {asset.id: asset for asset in run.assets}
        self.evidence = {item.id: item for item in run.evidence}
        by_asset = defaultdict(list)
        for item in run.evidence:
            by_asset[item.asset_id].append(item)
        self.locations = {
            asset.id: _precise_location(asset, by_asset[asset.id]) for asset in run.assets
        }
        self.collected = {
            location
            for asset_id, location in self.locations.items()
            if location is not None
            and self.assets[asset_id].status_code is not None
            and self.assets[asset_id].attributes.get("body_truncated") is False
        }


def _locations(index, finding):
    if not finding.asset_ids or any(not index.locations.get(i) for i in finding.asset_ids):
        return None
    return tuple(sorted({index.locations[i] for i in finding.asset_ids}))


def _identity(index, finding):
    locations = _locations(index, finding)
    evidence = index.evidence
    if not finding.evidence_ids or any(
        i not in evidence or evidence[i].asset_id not in finding.asset_ids
        for i in finding.evidence_ids
    ):
        return None
    return (
        (finding.dedup_key, finding.category, locations)
        if locations and finding.dedup_key.strip()
        else None
    )


def _observation(index, finding):
    assets = index.assets
    evidence = {
        key: item
        for key, item in index.evidence.items()
        if item.asset_id in assets and item.asset_id in finding.asset_ids
    }
    return ObservationReference(
        id=finding.id,
        run_id=index.run.id,
        title=_text(finding.title),
        category=_text(finding.category),
        severity=_text(finding.severity),
        confidence=_text(finding.confidence),
        summary=_text(finding.summary),
        asset_urls=sorted({_url(assets[i].url) for i in finding.asset_ids if i in assets}),
        evidence=[
            EvidenceReference(
                id=i, source_url=_url(evidence[i].source_url), summary=_text(evidence[i].summary)
            )
            for i in sorted(set(finding.evidence_ids))
            if i in evidence
        ],
        evidence_missing=not finding.evidence_ids
        or any(i not in evidence for i in finding.evidence_ids),
    )


def _options(run):
    if any(key not in run.options or run.options[key] is None for key in ScanOptions.model_fields):
        return None
    try:
        return ScanOptions.model_validate(run.options).model_dump()
    except ValidationError:
        return None


def _coverage_known(run):
    if (
        run.status != "completed"
        or run.completeness not in {"complete", "legacy_single_context"}
        or run.failures
        or run.error_code
        or not run.assets
    ):
        return False
    if len(run.contexts) != 1:
        return False
    context = run.contexts[0]
    if (
        context.kind != "anonymous"
        or context.status != "completed"
        or context.collection_status != "completed"
        or context.failure_count
        or context.error_code
        or context.completeness not in {"complete", "legacy_single_context"}
    ):
        return False
    for name in ("collect", "analyze"):
        stages = [stage for stage in run.stages if stage.name == name]
        if len(stages) != 1 or stages[0].status != "completed":
            return False
    return all(asset.attributes.get("body_truncated") is False for asset in run.assets)


def _comparability(source, current):
    reasons = []
    if source.normalized_url != current.normalized_url or source.target_id != current.target_id:
        reasons.append(ComparisonReason(code="scope_changed", message="两次入口或 Target 不一致。"))
    before, after = _options(source), _options(current)
    if before is None or after is None:
        reasons.append(
            ComparisonReason(code="options_unknown", message="历史预算或请求配置缺失、无效。")
        )
    elif before != after:
        reasons.append(
            ComparisonReason(code="options_changed", message="两次预算或请求配置发生变化。")
        )
    for side, run, label in (("source", source, "上次"), ("current", current, "本次")):
        if not _coverage_known(run):
            reasons.append(
                ComparisonReason(
                    code=f"{side}_coverage_unknown",
                    message=f"{label}有采集或分析缺项、失败、截断，或完整性记录不足。",
                )
            )
    return reasons


def _index(index, side):
    indexed = {}
    unverified = False
    for finding in sorted(index.run.findings, key=lambda finding: finding.id):
        identity = _identity(index, finding)
        if identity is None:
            identity = ("unverified", side, finding.id)
            unverified = True
        indexed[identity] = finding
    return indexed, unverified


def _collected(index, locations):
    return bool(locations) and set(locations) <= index.collected


_OPTION_LABELS = {
    "max_pages": "页面上限",
    "max_resources": "静态资源上限",
    "max_depth": "深度上限",
    "request_timeout_seconds": "单次请求超时（秒）",
    "overall_timeout_seconds": "整体超时（秒）",
    "retry_attempts": "重试次数",
    "max_redirects": "重定向上限",
    "user_agent": "User-Agent",
}


def _configuration(source, current):
    def display(key, value):
        if key == "user_agent":
            return "已记录（值不展示）" if isinstance(value, str) else "未记录"
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return str(value)
        return "未记录或无效"

    return [
        ConfigurationComparison(
            field=key,
            label=label,
            source=display(key, source.options.get(key)),
            current=display(key, current.options.get(key)),
            changed=source.options.get(key) != current.options.get(key),
        )
        for key, label in _OPTION_LABELS.items()
    ]


class ScanComparisonService:
    def compare(self, session, scan_run_id: int) -> ScanComparison:
        with session.no_autoflush:
            current = self._load(session, scan_run_id)
            if current.rerun_of_run_id is None or current.rerun_of_run_id == current.id:
                raise InputValidationError("此任务没有可用的重新扫描来源。")
            source = self._load(session, current.rerun_of_run_id)
            reasons = _comparability(source, current)
            source_index, current_index = _RunIndex(source), _RunIndex(current)
            before, old_unverified = _index(source_index, "source")
            after, new_unverified = _index(current_index, "current")
            if old_unverified or new_unverified:
                reasons.append(
                    ComparisonReason(
                        code="identity_unknown",
                        message="部分历史观察缺少可验证的规则、资产或证据关联；不会将缺失记录推断为变化。",
                    )
                )
            items = []
            for key in sorted(before.keys() | after.keys(), key=str):
                old, new = before.get(key), after.get(key)
                status = "persistent" if old and new else "new" if new else "not_observed"
                asset_uncovered = (not old or not new) and not _collected(
                    source_index if new else current_index,
                    _locations(current_index, new) if new else _locations(source_index, old),
                )
                if (reasons or asset_uncovered) and status != "persistent":
                    status = "unknown"
                items.append(
                    ObservationChange(
                        status=status,
                        reason={
                            "unknown": (
                                "另一轮未完整采集对应资产，无法判断该观察的变化。"
                                if asset_uncovered
                                else "两次范围、配置、覆盖或证据不可比，不能判断观察的新增或消失。"
                            ),
                            "persistent": "两次均有同规则、同位置的观察。",
                            "new": "本次观察到，上次没有对应记录。",
                            "not_observed": "上次有记录，本次未再观察到；不代表已修复。",
                        }[status],
                        before=_observation(source_index, old) if old else None,
                        after=_observation(current_index, new) if new else None,
                    )
                )
            counts = Counter(item.status for item in items)
            return ScanComparison(
                source=_run_view(source),
                current=_run_view(current),
                comparable=not reasons,
                reasons=reasons,
                configuration=_configuration(source, current),
                counts={key: counts[key] for key in _STATUSES},
                items=items,
            )

    def _load(self, session, run_id):
        run = session.scalar(
            select(ScanRun)
            .where(ScanRun.id == run_id)
            .options(
                *(
                    selectinload(getattr(ScanRun, name))
                    for name in ("assets", "evidence", "findings", "contexts", "stages", "failures")
                )
            )
        )
        if run is None:
            raise ResourceNotFoundError("任务或关联的来源任务不存在。")
        if run.mode != "quick" or run.active_checks_enabled:
            raise InputValidationError("首期对比仅支持被动匿名任务。")
        if run.status not in TERMINAL_SCAN_RUN_STATUSES:
            raise ConflictError("请等待两次任务都结束后再查看对比。")
        return run
