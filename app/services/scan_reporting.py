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
from app.services.scan_failure_classification import is_coverage_warning
from app.services.scan_report_quality import project_finding_groups, report_version_values


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
        coverage_warnings = [
            item for item in failures if is_coverage_warning(item.stage, item.code)
        ]
        request_failures = [
            item for item in failures if not is_coverage_warning(item.stage, item.code)
        ]
        assets = sorted(run.assets, key=lambda item: item.id)
        evidence = sorted(run.evidence, key=lambda item: item.id)
        findings = sorted(run.findings, key=lambda item: item.id)
        finding_groups = project_finding_groups(findings)
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
            f"- 风险或关注项：{len(finding_groups)} 类（{len(findings)} 条原始观察）",
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
        detailed_evidence = [item for item in evidence if not item.data.get("resource_summary")]
        resource_evidence = [item for item in evidence if item.data.get("resource_summary")]
        for item in detailed_evidence:
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
            preview = self._inline(str(item.data.get("preview") or "-"))[:500]
            evidence_lines.append(f"- 脱敏内容预览：{preview}")
            lines.extend([*evidence_lines, ""])
        if resource_evidence:
            lines.extend(["### 静态资源证据索引", ""])
        for item in resource_evidence:
            resource_summary = item.data["resource_summary"]
            versions = (
                ", ".join(report_version_values(item.source_url, resource_summary)) or "未识别"
            )
            signals = ", ".join(resource_summary.get("security_signals") or []) or "未观察到"
            truncated = bool(resource_summary.get("truncated"))
            hash_label = "采集片段 SHA-256" if truncated else "SHA-256"
            preview = str(item.data.get("preview") or "")
            content_note = (
                f"；内容说明={self._inline(preview)}"
                if preview.startswith("[non-text response omitted")
                else ""
            )
            asset_ref = f"A{item.asset_id}" if item.asset_id else "未关联"
            lines.append(
                f"- E{item.id}（{asset_ref}）资源摘要："
                f"size={resource_summary.get('size_bytes', 0)} bytes；"
                f"truncated={str(truncated).lower()}；"
                f"{hash_label}={resource_summary.get('sha256') or '-'}；"
                f"版本线索={versions}；安全信号={signals}；"
                f"来源={self._inline(item.source_url)}{content_note}"
            )
        if resource_evidence:
            lines.append("")

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
        for group in finding_groups:
            first = group.representative
            evidence_refs = self._sample_refs("E", list(group.evidence_ids))
            asset_refs = self._sample_refs("A", list(group.asset_ids))
            heading_id = f"F{first.id} 等" if group.observation_count > 1 else f"F{first.id}"
            lines.extend(
                [
                    f"### {heading_id}：{first.title}",
                    "",
                    f"- 严重性 / 置信度：{first.severity} / {first.confidence}",
                    f"- 类别：{first.category}",
                    (
                        f"- 影响范围：{len(group.asset_ids)} 个资产，"
                        f"{len(group.evidence_ids)} 条证据"
                    ),
                    f"- 关联资产样本：{asset_refs}",
                    f"- 关联证据样本：{evidence_refs}",
                    f"- 说明：{first.summary}",
                    f"- 建议：{first.remediation}",
                    "",
                ]
            )

        def report_status(stage) -> str:
            if stage.name == "report" and stage.status == "running":
                return "completed"
            return stage.status

        completed_stages = sum(
            1
            for stage in stages
            if report_status(stage) in {"completed", "completed_with_warnings"}
        )
        has_collection_buckets = any("collection_bucket" in item.data for item in evidence)
        if has_collection_buckets:
            page_requests = sum(item.data.get("collection_bucket") == "page" for item in evidence)
            resource_requests = sum(
                item.data.get("collection_bucket") == "resource" for item in evidence
            )
            request_coverage = (
                f"页面队列 {page_requests}/{run.options.get('max_pages', '-')}，"
                f"资源队列 {resource_requests}/{run.options.get('max_resources', '-')}"
            )
        else:
            final_pages = sum(asset.asset_type == "page" for asset in assets)
            final_resources = sum(asset.asset_type != "page" for asset in assets)
            request_coverage = (
                f"旧版记录未保存请求预算桶；仅可确认最终页面类型 {final_pages}，"
                f"其他资产类型 {final_resources}"
            )
        lines.extend(
            [
                "## 扫描覆盖范围",
                "",
                f"- 阶段完成：{completed_stages}/{len(stages)}",
                f"- 已请求并记录：{request_coverage}",
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
            lines.append("未记录由同源边界、页面/资源/深度预算或采集时限导致的覆盖告警。")
        for warning in coverage_warnings:
            location = f"（{warning.url}）" if warning.url else ""
            lines.append(
                f"- [{warning.stage}/{warning.code}] {warning.message}{location}；"
                f"尝试次数={warning.attempt}，可重试={str(warning.retryable).lower()}"
            )
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

    def _sample_refs(self, prefix: str, identifiers: list[int], limit: int = 10) -> str:
        if not identifiers:
            return "无"
        rendered = ", ".join(f"{prefix}{identifier}" for identifier in identifiers[:limit])
        if len(identifiers) > limit:
            rendered += f" 等（共 {len(identifiers)} 条）"
        return rendered

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
