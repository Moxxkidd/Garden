"""Shared user-facing names; values continue to come from queue projections."""

OPTION_LABELS = {
    "max_requests": "实际发送上限（含验证、重定向和重试）",
    "max_work": "工作项上限",
    "max_evidence_bytes": "证据上限（字节）",
    "max_variants": "每类路由参数变体上限",
    "origin_concurrency": "同站点并行采集上限",
    "min_interval_ms": "最小发送间隔（毫秒）",
    "retry_attempts": "单项重试上限",
}
STATE_LABELS = {
    "pending": "待采集",
    "leased": "处理中",
    "observed": "已观察",
    "deferred": "等待重试",
    "failed": "待恢复",
    "uncertain": "结果不确定",
    "suppressed": "受配额抑制",
    "transferred": "已转交新轮",
    "running": "执行中",
    "paused": "已暂停",
    "finished": "执行结束",
    "ready": "待执行",
}
