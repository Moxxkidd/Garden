"""Shared user-facing names; values continue to come from queue projections."""

from pydantic import TypeAdapter

from app.schemas.collection_queue import CollectionOptions

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


# Generate native form limits from the same schema used by API and execution.

COLLECTION_FIELDS = tuple(
    {"name": name, "label": OPTION_LABELS[name], **field}
    for name, field in CollectionOptions.model_json_schema()["properties"].items()
)
COLLECTION_FORM_KEYS = ("durable_enabled", *("durable_" + f["name"] for f in COLLECTION_FIELDS))


def collection_form_options(values):
    if not TypeAdapter(bool).validate_python(values.get("durable_enabled") or False):
        return None
    return CollectionOptions.model_validate(
        {
            field["name"]: values["durable_" + field["name"]]
            for field in COLLECTION_FIELDS
            if values.get("durable_" + field["name"], "") != ""
        }
    )


def collection_metric_lines(metrics):
    labels = (
        ("requests_used", "发送尝试"),
        ("possibly_sent", "可能已发送"),
        ("responses", "响应"),
        ("evidence_bytes", "证据字节"),
        ("confirmed_observations", "已确认观察"),
        ("verification_requests", "身份验证请求"),
        ("retry_requests", "重试发送"),
        ("uncertain_observations", "身份不确定观察"),
        ("diagnostic_observations", "登录诊断观察"),
        ("unknown_identity_observations", "身份状态未记录观察"),
        ("duplicate_candidates", "发现阶段重复候选"),
        ("duplicate_work_submissions", "重复入队"),
    )
    lines = [f"采集方式：{metrics.get('transport', '未记录')}"]
    for key, label in labels:
        value = metrics.get(key)
        lines.append(f"{label}：{value if value is not None else '未记录'}")
    for key, label in (
        ("queued_seconds", "排队耗时"),
        ("active_seconds", "活动采集耗时"),
        ("wait_seconds", "等待耗时"),
        ("cooldown_seconds", "其中冷却耗时"),
    ):
        value = metrics.get(key)
        lines.append(label + "：" + (f"{value:.3f} 秒" if value is not None else "未记录"))
    if metrics.get("timing_complete") is not True:
        lines.append("耗时仅为已提交样本，尚非完整计时；活动耗时不等于 CPU 时间。")
    ratio = metrics.get("confirmed_observations_per_request")
    lines.append(
        "本轮已确认观察 / 发送尝试："
        + (f"{ratio:.3f}" if ratio is not None else "未记录或分母为 0")
    )
    lines.append("观察指已保存的响应证据，不代表业务有效资产；发送尝试含可能已发送、验证与重试。")
    project = metrics.get("project_ingestion")
    if project is None:
        lines.append("项目入库结果：未记录或尚未完成收尾。")
    elif project["state"] == "not_applicable":
        lines.append("项目入库结果：不适用（收尾时未关联项目）。")
    else:
        lines.append("项目入库结果为本轮收尾快照；后续入库重试不改写历史指标。")
        for item in project["projects"]:
            subjects, observations = item["new_subjects"], item["new_observations"]
            lines.append(
                f"项目 #{item['project_id']}：新增项目主体："
                f"{subjects if subjects is not None else '未记录'}；新增项目观察："
                f"{observations if observations is not None else '未记录'}"
            )
        lines.append("新增主体按首次创建的上下文归因；其他上下文保留各自的新增观察。")
    return lines
