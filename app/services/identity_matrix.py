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
            verified = [r for r in observations if r.identity_assessment == "confirmed"]
            uncertain = [r for r in observations if r.identity_assessment == "identity_uncertain"]
            state = (
                "observed"
                if verified
                else "identity_uncertain"
                if uncertain
                else "not_observed"
                if context.completeness == "complete" and context.health.status == "ready"
                else "unknown"
            )
            cells[context.context_key] = IdentityMatrixCell(
                state=state,
                status_codes=sorted({n for r in observations for n in r.status_codes}),
                observation_ids=[r.asset_id for r in observations],
                evidence_ids=sorted({i for r in observations for i in (r.evidence_ids or [])}),
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
