"""Passive three-context coverage comparison."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.coverage_difference import CoverageDifference
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanRun
from app.services.coverage_identity import redacted_observed_url

CONTEXT_KINDS = ("anonymous", "user", "admin")


@dataclass(frozen=True)
class CoverageComparisonSummary:
    differences: tuple[CoverageDifference, ...]
    counts: dict[str, int]


class CoverageComparisonService:
    """Rebuild explainable passive differences for one authenticated run."""

    def compare(self, session: Session, run: ScanRun) -> CoverageComparisonSummary:
        contexts = {
            context.kind: context
            for context in session.scalars(
                select(ScanContext).where(ScanContext.scan_run_id == run.id)
            )
        }
        assets = list(
            session.scalars(
                select(ScanAsset)
                .where(
                    ScanAsset.scan_run_id == run.id,
                    ScanAsset.identity_key.is_not(None),
                )
                .order_by(ScanAsset.identity_key, ScanAsset.id)
            )
        )
        assets_by_identity: dict[str, dict[str, ScanAsset]] = {}
        context_kind_by_id = {context.id: context.kind for context in contexts.values()}
        for asset in assets:
            kind = context_kind_by_id.get(asset.context_id)
            if kind is None or asset.identity_key is None:
                continue
            assets_by_identity.setdefault(asset.identity_key, {})[kind] = asset

        for existing in list(
            session.scalars(
                select(CoverageDifference).where(CoverageDifference.scan_run_id == run.id)
            )
        ):
            session.delete(existing)
        session.flush()

        differences: list[CoverageDifference] = []
        for identity_key in sorted(assets_by_identity):
            context_assets = assets_by_identity[identity_key]
            states = {
                kind: self._state(contexts.get(kind), context_assets.get(kind))
                for kind in CONTEXT_KINDS
            }
            classification = self._classification(states, context_assets)
            difference = CoverageDifference(
                scan_run_id=run.id,
                identity_key=identity_key,
                classification=classification,
                anonymous_state=states["anonymous"],
                user_state=states["user"],
                admin_state=states["admin"],
                anonymous_present=self._present_value(states["anonymous"]),
                user_present=self._present_value(states["user"]),
                admin_present=self._present_value(states["admin"]),
                context_summaries={
                    kind: self._asset_summary(asset) for kind, asset in context_assets.items()
                },
                confidence=self._confidence(classification, states),
                diagnostic=self._diagnostic(classification),
            )
            session.add(difference)
            differences.append(difference)
        session.flush()
        counts = dict(Counter(item.classification for item in differences))
        return CoverageComparisonSummary(differences=tuple(differences), counts=counts)

    def _state(
        self,
        context: ScanContext | None,
        asset: ScanAsset | None,
    ) -> str:
        if asset is not None:
            return "present"
        if (
            context is not None
            and context.collection_status == "completed"
            and context.completeness == "complete"
        ):
            return "absent"
        return "unknown"

    def _classification(
        self,
        states: dict[str, str],
        context_assets: dict[str, ScanAsset],
    ) -> str:
        if "unknown" in states.values():
            return "unknown"
        pattern = tuple(states[kind] == "present" for kind in CONTEXT_KINDS)
        if pattern == (True, True, True):
            summaries = [self._stable_fields(context_assets[kind]) for kind in CONTEXT_KINDS]
            return "shared" if summaries[0] == summaries[1] == summaries[2] else "inconsistent"
        if pattern == (False, False, True):
            return "admin_only"
        if pattern == (False, True, True):
            return "user_only"
        if pattern == (False, True, False):
            return "inconsistent"
        if pattern[0]:
            return "unexpectedly_anonymous"
        return "inconsistent"

    def _asset_summary(self, asset: ScanAsset) -> dict[str, object]:
        return {"asset_id": asset.id, **self._stable_fields(asset)}

    def _stable_fields(self, asset: ScanAsset) -> dict[str, object]:
        attributes = asset.attributes if isinstance(asset.attributes, dict) else {}
        content_type = attributes.get("content_type")
        if isinstance(content_type, str):
            content_type = content_type.split(";", maxsplit=1)[0].strip().lower() or None
        return {
            "status_code": asset.status_code,
            "url": redacted_observed_url(asset.url),
            "title": asset.title,
            "content_type": content_type,
            "content_signature": attributes.get("content_signature"),
        }

    def _present_value(self, state: str) -> bool | None:
        if state == "unknown":
            return None
        return state == "present"

    def _confidence(self, classification: str, states: dict[str, str]) -> str:
        if classification == "unknown":
            return "low"
        if classification == "inconsistent" and all(
            state == "present" for state in states.values()
        ):
            return "medium"
        return "high"

    def _diagnostic(self, classification: str) -> str:
        messages = {
            "shared": "三个完整上下文均观察到相同的稳定资产响应。",
            "user_only": "匿名上下文未观察到该资产，用户与管理员上下文已观察到。",
            "admin_only": "仅管理员上下文观察到该资产。",
            "unexpectedly_anonymous": "匿名上下文观察到该资产，但更高权限上下文存在缺失。",
            "inconsistent": "上下文间的资产存在顺序或稳定响应属性不一致。",
            "unknown": "至少一个上下文不完整，无法判断该资产是否缺失。",
        }
        return messages[classification]
