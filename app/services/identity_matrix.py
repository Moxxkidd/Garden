"""Stable identity columns over per-run route groups; never infer absence."""

from app.schemas.identity import IdentityMatrix, IdentityMatrixCell, IdentityMatrixRow
from app.services.asset_grouping import group_records

CELL_LABELS = {
    "observed": "已观察",
    "identity_uncertain": "身份不确定",
    "not_observed": "未观察",
    "unknown": "未知",
}


def build_identity_matrix(records, contexts):
    groups = group_records([r for r in records if r.identity_assessment != "auth_diagnostic"], {})
    rows, confirmed = [], 0
    for group in groups:
        cells = {}
        for context in contexts:
            observations = [r for r in group.observations if r.context == context.context_key]
            proofs = []
            for row in observations:
                if row.identity_observations:
                    proofs.extend(row.identity_observations)
                else:
                    proofs.extend(
                        {
                            "assessment": row.identity_assessment,
                            "status_code": code,
                            "evidence_ids": row.evidence_ids or [],
                        }
                        for code in (row.status_codes or [None])
                    )
            verified = [p for p in proofs if p.get("assessment") == "confirmed"]
            uncertain = [p for p in proofs if p.get("assessment") == "identity_uncertain"]
            state = (
                "observed"
                if verified
                else "identity_uncertain"
                if uncertain
                else "unknown"
                if observations
                else "not_observed"
                if context.completeness == "complete" and context.health.status == "ready"
                else "unknown"
            )

            def codes(items):
                return sorted(
                    {
                        p["status_code"]
                        for p in items
                        if type(p.get("status_code")) is int and 100 <= p["status_code"] <= 599
                    }
                )

            def evidence(items):
                return sorted(
                    {
                        i
                        for p in items
                        for i in (
                            [p["evidence_id"]]
                            if p.get("evidence_id") is not None
                            else p.get("evidence_ids", [])
                        )
                        if type(i) is int
                    }
                )

            cells[context.context_key] = IdentityMatrixCell(
                state=state,
                status_codes=codes(verified),
                evidence_ids=evidence(verified),
                uncertain_status_codes=codes(uncertain),
                uncertain_evidence_ids=evidence(uncertain),
                observation_ids=[r.asset_id for r in observations],
            )
        confirmed += any(c.state == "observed" for c in cells.values())
        rows.append(
            IdentityMatrixRow(
                asset_id=group.asset_id, url=group.url, method=group.method, cells=cells
            )
        )
    return IdentityMatrix(
        contexts=contexts,
        rows=rows,
        confirmed_subject_count=confirmed,
        auth_diagnostic_count=sum(r.identity_assessment == "auth_diagnostic" for r in records),
    )


def matrix_summary(matrix):
    return (
        f"已确认身份下观察到 {matrix['confirmed_subject_count']} 类资产主体；"
        f"{matrix['auth_diagnostic_count']} 条认证诊断不计入该数。"
    )
