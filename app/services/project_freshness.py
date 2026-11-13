"""Read-only age hints and the existing conservative comparison, never liveness proof."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.errors import InputValidationError
from app.models.project import ProjectRun
from app.models.project_asset import ProjectObservation
from app.services.project_comparison import ProjectComparisonService
from app.services.project_source import canonical_value
from app.services.projects import ProjectService


def utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def age_label(seen, now, days):
    if seen is None:
        return "unknown"
    when = datetime.fromisoformat(seen) if isinstance(seen, str) else seen
    return "older" if utc(when) < utc(now) - timedelta(days=days) else "recent"


class ProjectFreshnessService:
    def __init__(self, *, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def configure(self, session, project_id, days, expected_revision):
        if not 1 <= days <= 3650:
            raise InputValidationError("新鲜度天数须为 1—3650。")
        project = ProjectService().bump(session, project_id, expected_revision)
        project.freshness_days = days
        session.flush()
        return project

    def view(self, session, project_id):
        project = ProjectService().get(session, project_id)
        latest = (
            select(func.max(ProjectObservation.seen_at).label("seen"))
            .where(ProjectObservation.project_id == project_id)
            .group_by(ProjectObservation.display_asset_id)
            .subquery()
        )
        cutoff = utc(self.clock()) - timedelta(days=project.freshness_days)
        older = session.scalar(
            select(func.count()).select_from(latest).where(latest.c.seen < cutoff)
        )
        unknown = session.scalar(
            select(func.count()).select_from(latest).where(latest.c.seen.is_(None))
        )
        # Each entry/source has its own pair; comparing different entries would erase meaning.
        groups = {}
        for run in session.scalars(
            select(ProjectRun)
            .where(ProjectRun.project_id == project_id, ProjectRun.status == "complete")
            .order_by(ProjectRun.id.desc())
        ):
            pair = groups.setdefault((run.entry_id, run.source), [])
            if len(pair) < 2:
                pair.append(run)
        comparisons, sources = [], []
        for (entry_id, source), pair in groups.items():
            sources.append(
                dict(
                    project_run_id=pair[0].id,
                    entry_id=entry_id,
                    source=source,
                    scope=pair[0].snapshot["scope"],
                    finished_at=pair[0].snapshot.get("comparison_metadata", {}).get("finished_at"),
                    budget=pair[0].snapshot.get("comparison_metadata", {}).get("budget"),
                )
            )
            if len(pair) == 2:
                result = ProjectComparisonService().compare(
                    session, project_id, pair[1].id, pair[0].id
                )
                comparisons.append(result.model_dump(mode="json"))
        return dict(
            last_observed_at=canonical_value(
                session.scalar(
                    select(func.max(ProjectObservation.seen_at)).where(
                        ProjectObservation.project_id == project_id
                    )
                )
            ),
            freshness_days=project.freshness_days,
            older_assets=older,
            unknown_time_assets=unknown,
            checked_at=utc(self.clock()).isoformat(),
            latest_sources=sources,
            comparisons=comparisons,
            note="较旧仅表示超过设定天数未观察；未再次观察不等于删除或无效。",
        )
