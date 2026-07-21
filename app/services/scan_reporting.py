"""Structured Markdown reporting for complete URL scan runs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ResourceNotFoundError
from app.models.scan_run import ScanRun


class ScanReportService:
    """Render persisted domain records; CLI text is never an input."""

    def __init__(self, output_root: Path | None = None) -> None:
        self.output_root = output_root or (Path.cwd() / "exports" / "scan-reports")

    def generate(self, session: Session, scan_run_id: int) -> str:
        run = session.scalar(
            select(ScanRun)
            .where(ScanRun.id == scan_run_id)
            .options(
                selectinload(ScanRun.stages),
                selectinload(ScanRun.assets),
                selectinload(ScanRun.evidence),
                selectinload(ScanRun.findings),
                selectinload(ScanRun.failures),
            )
        )
        if run is None:
            raise ResourceNotFoundError(f"Scan run {scan_run_id} was not found.")
        generated_at = datetime.now(timezone.utc)
        lines = self._render(run, generated_at)
        self.output_root.mkdir(parents=True, exist_ok=True)
        path = self.output_root / f"scan-{run.id}.md"
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        run.report_path = str(path)
        run.report_generated_at = generated_at
        session.flush()
        return str(path)

    def _render(self, run: ScanRun, generated_at: datetime) -> list[str]:
        failures = sorted(run.failures, key=lambda item: item.id)
        assets = sorted(run.assets, key=lambda item: item.id)
        evidence = sorted(run.evidence, key=lambda item: item.id)
        findings = sorted(run.findings, key=lambda item: item.id)
        stages = sorted(run.stages, key=lambda item: item.position)
        warning_count = len(failures)
        summary_status = run.status
        if summary_status == "running":
            summary_status = "completed_with_warnings" if failures else "completed"
        lines = [
            f"# Garden 资产报告 #{run.id}",
            "",
            "## 执行摘要",
            "",
            f"- 任务状态：{summary_status}",
            f"- 入口 URL：{run.normalized_url}",
            f"- 发现资产：{len(assets)}",
            f"- 证据记录：{len(evidence)}",
            f"- 风险或关注项：{len(findings)}",
            f"- 失败或未覆盖项：{warning_count}",
            "",
            "## 扫描范围",
            "",
            f"- 起始地址：{run.normalized_url}",
            f"- 最大页面数：{run.options.get('max_pages', '-')}",
            f"- 最大深度：{run.options.get('max_depth', '-')}",
            f"- 单请求超时：{run.options.get('request_timeout_seconds', '-')} 秒",
            f"- 整体超时：{run.options.get('overall_timeout_seconds', '-')} 秒",
            "- 请求方法：仅被动 GET；不执行利用、写入或破坏性操作",
            "- 边界：仅跟随同源页面；每次连接和重定向均重新执行地址策略校验",
            "",
            "## 发现的资产",
            "",
        ]
        if not assets:
            lines.append("未发现可记录的资产。")
        else:
            lines.extend(["| ID | 类型 | 状态 | 标题 | URL |", "|---:|---|---:|---|---|"])
            for asset in assets:
                lines.append(
                    f"| A{asset.id} | {asset.asset_type} | {asset.status_code or '-'} | "
                    f"{self._cell(asset.title or '-')} | {self._cell(asset.url)} |"
                )
        lines.extend(["", "## 关键属性", ""])
        if not assets:
            lines.append("无可归纳的关键属性。")
        for asset in assets:
            content_type = asset.attributes.get("content_type") or "-"
            elapsed = asset.attributes.get("elapsed_ms")
            attempts = asset.attributes.get("attempts")
            lines.append(
                f"- A{asset.id}：content-type={content_type}，耗时={elapsed or 0}ms，"
                f"请求尝试={attempts or 1}"
            )
        lines.extend(["", "## 证据", ""])
        if not evidence:
            lines.append("没有可用证据；请结合失败与未覆盖部分判断报告完整性。")
        for item in evidence:
            headers = item.data.get("headers", {})
            rendered_headers = ", ".join(
                f"{key}={self._inline(str(value))}" for key, value in sorted(headers.items())[:12]
            )
            preview = self._inline(str(item.data.get("preview") or "-"))[:500]
            lines.extend(
                [
                    f"### E{item.id}：{item.title}",
                    "",
                    f"- 来源：{item.source_url}",
                    f"- 资产：A{item.asset_id}" if item.asset_id else "- 资产：未关联",
                    f"- 摘要：{item.summary}",
                    f"- 响应头证据：{rendered_headers or '-'}",
                    f"- 脱敏内容预览：{preview}",
                    "",
                ]
            )
        lines.extend(["## 风险或关注项", ""])
        if not findings:
            lines.append("未识别到风险或关注项。这不代表目标不存在未覆盖风险。")
        for finding in findings:
            evidence_refs = ", ".join(f"E{item}" for item in finding.evidence_ids) or "无"
            asset_refs = ", ".join(f"A{item}" for item in finding.asset_ids) or "无"
            lines.extend(
                [
                    f"### F{finding.id}：{finding.title}",
                    "",
                    f"- 严重性 / 置信度：{finding.severity} / {finding.confidence}",
                    f"- 类别：{finding.category}",
                    f"- 关联资产：{asset_refs}",
                    f"- 关联证据：{evidence_refs}",
                    f"- 说明：{finding.summary}",
                    f"- 建议：{finding.remediation}",
                    "",
                ]
            )

        def report_status(stage) -> str:
            if stage.name == "report" and stage.status == "running":
                return "completed"
            return stage.status

        completed_stages = sum(1 for stage in stages if report_status(stage) == "completed")
        lines.extend(
            [
                "## 扫描覆盖范围",
                "",
                f"- 阶段完成：{completed_stages}/{len(stages)}",
                f"- 已请求并记录的页面：{len(assets)}/{run.options.get('max_pages', '-')}",
            ]
        )
        for stage in stages:
            summary = stage.summary
            if stage.name == "report" and stage.status == "running":
                summary = "本结构化报告已生成。"
            lines.append(f"- {stage.name}：{report_status(stage)}；{summary or '-'}")
        lines.extend(["", "## 失败或未覆盖部分", ""])
        if not failures:
            lines.append("未记录阶段失败或单页采集失败。")
        for failure in failures:
            location = f"（{failure.url}）" if failure.url else ""
            lines.append(
                f"- [{failure.stage}/{failure.code}] {failure.message}{location}；"
                f"尝试次数={failure.attempt}，可重试={str(failure.retryable).lower()}"
            )
        lines.extend(
            [
                "",
                "## 生成时间",
                "",
                f"- {generated_at.isoformat()}",
                "",
                "---",
                "本报告仅反映上述边界内的被动资产发现结果，供已授权目标的审阅使用。",
            ]
        )
        return lines

    def _cell(self, value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    def _inline(self, value: str) -> str:
        return " ".join(value.replace("|", "\\|").split())
