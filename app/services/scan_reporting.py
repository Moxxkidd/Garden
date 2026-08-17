"""Structured Markdown reporting for complete URL scan runs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.cli.paths import formal_runtime_paths
from app.core.errors import ResourceNotFoundError
from app.models.scan_run import ScanRun


class ScanReportService:
    """Render persisted domain records; CLI text is never an input."""

    _control_character_pattern = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

    def __init__(self, output_root: Path | None = None) -> None:
        formal_paths = formal_runtime_paths()
        self.output_root = output_root or (
            formal_paths.reports_dir
            if formal_paths is not None
            else Path.cwd() / "exports" / "scan-reports"
        )

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
        coverage_warnings = [item for item in failures if item.code == "coverage_limit_reached"]
        request_failures = [item for item in failures if item.code != "coverage_limit_reached"]
        assets = sorted(run.assets, key=lambda item: item.id)
        evidence = sorted(run.evidence, key=lambda item: item.id)
        findings = sorted(run.findings, key=lambda item: item.id)
        stages = sorted(run.stages, key=lambda item: item.position)
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
            f"- 覆盖告警：{len(coverage_warnings)}",
            f"- 请求或阶段失败：{len(request_failures)}",
            "",
            "## 扫描范围",
            "",
            f"- 起始地址：{run.normalized_url}",
            f"- 最大页面数：{run.options.get('max_pages', '-')}",
            f"- 最大静态资源数：{run.options.get('max_resources', '-')}",
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
            evidence_lines = [
                f"### E{item.id}：{item.title}",
                "",
                f"- 来源：{item.source_url}",
                f"- 资产：A{item.asset_id}" if item.asset_id else "- 资产：未关联",
                f"- 摘要：{item.summary}",
                f"- 响应头证据：{rendered_headers or '-'}",
            ]
            resource_summary = item.data.get("resource_summary")
            if resource_summary:
                versions = ", ".join(resource_summary.get("version_hints") or []) or "未识别"
                signals = ", ".join(resource_summary.get("security_signals") or []) or "未观察到"
                evidence_lines.append(
                    "- 资源摘要："
                    f"size={resource_summary.get('size_bytes', 0)} bytes；"
                    f"SHA-256={resource_summary.get('sha256') or '-'}；"
                    f"版本线索={versions}；安全信号={signals}"
                )
                preview = str(item.data.get("preview") or "")
                if preview.startswith("[non-text response omitted"):
                    evidence_lines.append(f"- 内容说明：{self._inline(preview)}")
            else:
                preview = self._inline(str(item.data.get("preview") or "-"))[:500]
                evidence_lines.append(f"- 脱敏内容预览：{preview}")
            lines.extend([*evidence_lines, ""])

        observed_controls = self._observed_security_controls(evidence)
        lines.extend(["## 已观察到的安全控制", ""])
        if not observed_controls:
            lines.append("在本次采集的 HTML 响应中未观察到可确认的响应头安全控制。")
        else:
            for name, value in observed_controls:
                lines.append(f"- {name}={self._inline(value)}")
        lines.append("")
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
                "- 已请求并记录："
                f"页面 {sum(asset.asset_type == 'page' for asset in assets)}/"
                f"{run.options.get('max_pages', '-')}，静态资源 "
                f"{sum(asset.asset_type != 'page' for asset in assets)}/"
                f"{run.options.get('max_resources', '-')}",
            ]
        )
        for stage in stages:
            summary = stage.summary
            if stage.name == "report" and stage.status == "running":
                summary = "本结构化报告已生成。"
            lines.append(f"- {stage.name}：{report_status(stage)}；{summary or '-'}")
        lines.extend(["", "## 失败或未覆盖部分", ""])
        lines.append("覆盖边界告警与实际请求失败分开列示，避免把预算耗尽误判为网络失败。")
        lines.extend(["", "## 覆盖告警", ""])
        if not coverage_warnings:
            lines.append("未记录由页面、资源或深度预算导致的覆盖告警。")
        for warning in coverage_warnings:
            lines.append(f"- [{warning.stage}/{warning.code}] {warning.message}")
        lines.extend(["", "## 请求失败", ""])
        if not request_failures:
            lines.append("未记录阶段失败或单个 URL 请求失败。")
        for failure in request_failures:
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

    def _observed_security_controls(self, evidence) -> list[tuple[str, str]]:
        names = {
            "strict-transport-security": "Strict-Transport-Security",
            "content-security-policy": "Content-Security-Policy",
            "x-frame-options": "X-Frame-Options",
            "x-content-type-options": "X-Content-Type-Options",
            "referrer-policy": "Referrer-Policy",
            "permissions-policy": "Permissions-Policy",
        }
        observed: dict[str, str] = {}
        for item in evidence:
            headers = item.data.get("headers", {})
            content_type = str(headers.get("content-type", "")).lower()
            if "html" not in content_type:
                continue
            for key, label in names.items():
                value = headers.get(key)
                if value and label not in observed:
                    observed[label] = str(value)
        return list(observed.items())

    def _cell(self, value: str) -> str:
        return self._clean(value).replace("|", "\\|").replace("\n", " ")

    def _inline(self, value: str) -> str:
        return " ".join(self._clean(value).replace("|", "\\|").split())

    def _clean(self, value: str) -> str:
        return self._control_character_pattern.sub(" ", value)
