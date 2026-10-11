"""Conservative comparison of immutable source snapshots, independent of curation."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from app.core.errors import ConflictError, ResourceNotFoundError
from app.models.project import ProjectRun
from app.models.project_asset import ProjectObservation
from app.schemas.projects import ProjectComparison
from app.services.project_catalog import ProjectCatalogService
from app.services.project_source import canonical_time, digest
from app.services.projects import ProjectService

STATES = ("newly_observed", "attributes_changed", "unchanged", "not_observed", "unknown")


class ProjectComparisonService:
    def _run(self, session, project_id, run_id):
        run = session.get(ProjectRun, run_id)
        if not run or run.project_id != project_id:
            raise ResourceNotFoundError("project_run_missing")
        if run.status != "complete":
            raise ConflictError("project_run_not_indexed")
        return run

    def _reasons(self, before, after):
        reasons = []
        left, right = before.snapshot, after.snapshot
        a, b = left["scope"], right["scope"]
        x, y = left["comparison_metadata"], right["comparison_metadata"]
        if before.entry_id != after.entry_id or before.source != after.source:
            reasons.append("entry_or_source_changed")
        for field in ("target_id", "mode", "entry_url"):
            if a.get(field) != b.get(field) or a.get(field) is None:
                reasons.append(f"{field}_not_comparable")
        for field in ("configuration", "entry", "rule_version"):
            if not x.get(field) or x.get(field) != y.get(field):
                reasons.append(f"{field}_changed")
        if x.get("recovery") or y.get("recovery"):
            reasons.append("partial_recovery")
        try:
            earlier = canonical_time(datetime.fromisoformat(x["finished_at"]))
            later = canonical_time(datetime.fromisoformat(y["finished_at"]))
            if earlier >= later:
                reasons.append("source_order_unknown")
        except (KeyError, TypeError, ValueError):
            reasons.append("source_time_unknown")
        for scope in (a, b):
            if scope.get("completeness") != "complete":
                reasons.append("coverage_incomplete")
            if scope.get("status") not in {"completed", "completed_with_warnings"}:
                reasons.append("source_not_successful")
        return sorted(set(reasons))

    def _context_complete(self, scope, identity_key):
        for context in scope.get("contexts", []):
            if context.get("profile_id") is not None:
                key_matches = (
                    identity_key
                    == f"target:{scope.get('target_id')}:profile:{context['profile_id']}"
                )
            else:
                key_matches = context.get("kind") == "anonymous" and identity_key.endswith(
                    ":anonymous"
                )
            if key_matches:
                return (
                    context.get("completeness") == "complete"
                    and context.get("status") in {"completed", "ready"}
                    and (
                        context.get("health_status") in {"ready", "valid", "healthy"}
                        or (
                            context.get("kind") == "anonymous"
                            and context.get("profile_id") is None
                            and context.get("health_status") is None
                        )
                    )
                )
        return False

    def compare(self, session, project_id, before_project_run_id, after_project_run_id):
        ProjectService().get(session, project_id)
        before = self._run(session, project_id, before_project_run_id)
        after = self._run(session, project_id, after_project_run_id)
        reasons = self._reasons(before, after)
        indexes = []
        for run in (before, after):
            index = defaultdict(list)
            for observation in session.scalars(
                select(ProjectObservation)
                .where(ProjectObservation.project_run_id == run.id)
                .order_by(ProjectObservation.id)
            ):
                index[(observation.asset_id, observation.identity_key)].append(observation)
            indexes.append(index)
        left, right = indexes
        counts = dict.fromkeys(STATES, 0)
        items = []
        catalog = ProjectCatalogService()
        for key in sorted(left.keys() | right.keys()):
            previous, current = left[key], right[key]
            limitations = list(reasons)
            if not all(
                self._context_complete(r.snapshot["scope"], key[1]) for r in (before, after)
            ):
                limitations.append("identity_coverage_unknown")
            if any(
                o.record.get("observation") != "response_observed"
                or o.record.get("identity_assessment") in {"identity_uncertain", "auth_diagnostic"}
                or (
                    o.record.get("profile_id") is not None
                    and o.record.get("identity_assessment") != "confirmed"
                )
                for o in previous + current
            ):
                limitations.append("observation_identity_unknown")

            def attributes(rows):
                return {
                    digest(
                        {
                            field: row.record.get(field)
                            for field in (
                                "status_codes",
                                "content_type",
                                "title",
                                "identity_assessment",
                                "observation",
                            )
                        }
                    )
                    for row in rows
                }

            changed = bool(previous and current and attributes(previous) != attributes(current))
            if limitations:
                status = "unknown"
            elif not previous:
                status = "newly_observed"
            elif not current:
                status = "not_observed"
            else:
                status = "attributes_changed" if changed else "unchanged"
            counts[status] += 1
            items.append(
                dict(
                    asset_id=key[0],
                    identity_key=key[1],
                    status=status,
                    observed_attributes_differ=changed,
                    reasons=limitations,
                    before=[catalog._observation(r) for r in previous],
                    after=[catalog._observation(r) for r in current],
                )
            )
        return ProjectComparison(
            before_run_id=before.id,
            after_run_id=after.id,
            items=items,
            counts=counts,
            limitations=reasons + ["未再次观察到不等于已删除或已下线；比较仅覆盖所选两次任务。"],
        )
