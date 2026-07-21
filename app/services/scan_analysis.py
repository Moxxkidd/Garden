"""Passive, explainable analysis over normalized scan records."""

from __future__ import annotations

import hashlib

from app.models.scan_run import ScanAsset, ScanEvidence
from app.schemas.scan import AnalysisCandidate


class PassiveScanAnalyzer:
    SECURITY_HEADERS = {
        "content-security-policy": "Content-Security-Policy",
        "x-content-type-options": "X-Content-Type-Options",
    }

    def analyze(
        self, assets: list[ScanAsset], evidence: list[ScanEvidence]
    ) -> list[AnalysisCandidate]:
        evidence_by_asset = {item.asset_id: item for item in evidence if item.asset_id is not None}
        candidates: list[AnalysisCandidate] = []
        for asset in assets:
            item = evidence_by_asset.get(asset.id)
            headers = item.data.get("headers", {}) if item else {}
            evidence_ids = [item.id] if item else []
            content_type = str(asset.attributes.get("content_type") or "").lower()
            if asset.status_code is not None and asset.status_code >= 400:
                candidates.append(
                    self._candidate(
                        asset,
                        evidence_ids,
                        "http-error",
                        f"页面返回 HTTP {asset.status_code}",
                        "availability",
                        "low" if asset.status_code < 500 else "medium",
                        "high",
                        "扫描范围内的页面返回错误状态，相关资产可能不可用或需要授权。",
                        "确认该状态是否符合预期，并记录认证或访问控制要求。",
                    )
                )
            if "html" in content_type:
                for key, display in self.SECURITY_HEADERS.items():
                    if key not in headers:
                        candidates.append(
                            self._candidate(
                                asset,
                                evidence_ids,
                                f"missing-{key}",
                                f"缺少 {display} 响应头",
                                "security-headers",
                                "low",
                                "high",
                                f"HTML 响应未包含 {display}，这是被动观察结果。",
                                f"评估并在应用或边缘层配置合适的 {display} 策略。",
                            )
                        )
            preview = item.data.get("preview", "") if item else ""
            if asset.url.startswith("http://") and 'type="password"' in str(preview).lower():
                candidates.append(
                    self._candidate(
                        asset,
                        evidence_ids,
                        "password-over-http",
                        "HTTP 页面包含密码输入框",
                        "transport-security",
                        "high",
                        "high",
                        "未加密 HTTP 页面包含密码输入控件，凭据可能在传输中暴露。",
                        "使用 HTTPS，并将 HTTP 请求重定向至经过验证的 HTTPS 入口。",
                    )
                )
        return candidates

    def _candidate(
        self,
        asset: ScanAsset,
        evidence_ids: list[int],
        rule: str,
        title: str,
        category: str,
        severity: str,
        confidence: str,
        summary: str,
        remediation: str,
    ) -> AnalysisCandidate:
        digest = hashlib.sha256(f"{rule}|{asset.url}".encode()).hexdigest()[:32]
        return AnalysisCandidate(
            dedup_key=digest,
            title=title,
            category=category,
            severity=severity,
            confidence=confidence,
            summary=summary,
            remediation=remediation,
            asset_ids=[asset.id],
            evidence_ids=evidence_ids,
        )
