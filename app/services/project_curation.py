"""Reversible display corrections with immutable observation ownership and audit."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.models.project_asset import ProjectAsset, ProjectObservation
from app.models.project_curation import (
    ProjectAnnotation,
    ProjectAudit,
    ProjectCorrection,
    ProjectCorrectionMember,
)
from app.schemas.projects import ProjectCorrectionPreview
from app.services.asset_catalog import safe_text
from app.services.project_tokens import ProjectTokens
from app.services.project_transactions import atomic_import
from app.services.projects import ProjectService


class ProjectCurationService:
    def __init__(self, tokens=None):
        self.tokens = tokens or ProjectTokens()
        self.projects = ProjectService()

    def _selection(self, session, project_id, request):
        self.projects.writable(session, project_id)
        ids = request.asset_ids
        if len(set(ids)) != len(ids):
            raise InputValidationError("duplicate_project_assets")
        assets = list(
            session.scalars(
                select(ProjectAsset).where(
                    ProjectAsset.project_id == project_id, ProjectAsset.id.in_(ids)
                )
            )
        )
        if len(assets) != len(ids):
            raise ResourceNotFoundError("project_asset_not_found")
        signatures = {(a.site, a.method, a.original_type, a.rule_version) for a in assets}
        if len(signatures) != 1 or any(a.site is None or a.method is None for a in assets):
            raise ConflictError("incompatible_project_assets")
        if request.action == "split":
            if len(ids) != 1 or request.target_asset_id is not None or not request.observation_ids:
                raise InputValidationError("invalid_split_selection")
            rows = list(
                session.scalars(
                    select(ProjectObservation)
                    .where(
                        ProjectObservation.project_id == project_id,
                        ProjectObservation.display_asset_id == ids[0],
                        ProjectObservation.id.in_(request.observation_ids),
                    )
                    .order_by(ProjectObservation.id)
                )
            )
            total = session.scalar(
                select(func.count())
                .select_from(ProjectObservation)
                .where(
                    ProjectObservation.project_id == project_id,
                    ProjectObservation.display_asset_id == ids[0],
                )
            )
            if len(rows) != len(set(request.observation_ids)) or len(rows) >= total:
                raise InputValidationError("split_requires_proper_subset")
            return assets, rows
        if len(ids) < 2 or request.target_asset_id not in ids:
            raise InputValidationError("invalid_merge_selection")
        if request.observation_ids:
            raise InputValidationError("merge_requires_whole_subjects")
        rows = list(
            session.scalars(
                select(ProjectObservation)
                .where(
                    ProjectObservation.project_id == project_id,
                    ProjectObservation.display_asset_id.in_(ids),
                )
                .order_by(ProjectObservation.id)
            )
        )
        if {r.display_asset_id for r in rows} != set(ids):
            raise ConflictError("asset_is_alias_or_empty")
        active = list(
            session.scalars(
                select(ProjectCorrection).where(
                    ProjectCorrection.project_id == project_id, ProjectCorrection.status == "active"
                )
            )
        )
        # An aliased source cannot become its own successor through another merge.
        for correction in active:
            if correction.action == "merge" and any(i in correction.source_asset_ids for i in ids):
                raise ConflictError("asset_is_alias_or_cycle")
        return assets, rows

    def preview(self, session, project_id, request):
        assets, rows = self._selection(session, project_id, request)
        project = self.projects.get(session, project_id)
        count = session.scalar(
            select(func.count(func.distinct(ProjectObservation.display_asset_id))).where(
                ProjectObservation.project_id == project_id
            )
        )
        destination = uuid4().hex if request.action == "split" else request.target_asset_id
        return ProjectCorrectionPreview(
            action=request.action,
            target_asset_id=destination,
            assignments=[
                dict(
                    observation_id=row.id,
                    original_asset_id=row.asset_id,
                    from_asset_id=row.display_asset_id,
                    to_asset_id=destination,
                    project_run_id=row.project_run_id,
                    identity_key=row.identity_key,
                    url=row.record["url"],
                )
                for row in rows
            ],
            token=self.tokens.put(
                (project_id, request.model_copy(deep=True), project.revision, destination)
            ),
            before_subject_count=count,
            after_subject_count=count + 1 if request.action == "split" else count - len(assets) + 1,
            affected_observation_count=len(rows),
            limitations=["仅改变展示归属，保留原始观察；标签仍附着原资产 ID。"],
        )

    def confirm(self, session, project_id, preview_token):
        owner, request, revision, destination = self.tokens.get(preview_token)
        if owner != project_id:
            raise ConflictError("project_preview_mismatch")
        with atomic_import(session):
            assets, rows = self._selection(session, project_id, request)
            project = self.projects.bump(session, project_id, revision)
            target_id = request.target_asset_id
            if request.action == "split":
                source = assets[0]
                target = ProjectAsset(
                    id=destination,
                    project_id=project_id,
                    natural_key="manual:" + uuid4().hex,
                    rule_version=source.rule_version,
                    site=source.site,
                    method=source.method,
                    kind=source.kind,
                    original_type=source.original_type,
                    url=source.url,
                    manual=True,
                )
                session.add(target)
                session.flush()
                target_id = target.id
            correction = ProjectCorrection(
                project_id=project_id,
                action=request.action,
                target_asset_id=target_id,
                source_asset_ids=[i for i in request.asset_ids if i != request.target_asset_id],
                observation_ids=request.observation_ids,
                reason=safe_text(request.reason, limit=2000),
                revision=project.revision,
            )
            session.add(correction)
            session.flush()
            for row in rows:
                if row.display_asset_id == correction.target_asset_id:
                    continue
                session.add(
                    ProjectCorrectionMember(
                        correction_id=correction.id,
                        observation_id=row.id,
                        previous_asset_id=row.display_asset_id,
                        next_asset_id=correction.target_asset_id,
                    )
                )
                row.display_asset_id = correction.target_asset_id
            session.add(
                ProjectAudit(
                    project_id=project_id,
                    action=request.action,
                    detail={
                        "correction_id": correction.id,
                        "revision": project.revision,
                        "reason": correction.reason,
                    },
                )
            )
            session.flush()
            return correction

    def revoke(self, session, project_id, correction_id, expected_revision, reason):
        if not reason.strip() or len(reason) > 2000:
            raise InputValidationError("invalid_correction_reason")
        with atomic_import(session):
            correction = session.get(ProjectCorrection, correction_id)
            if not correction or correction.project_id != project_id:
                raise ResourceNotFoundError("project_correction_missing")
            if correction.status != "active":
                raise ConflictError("correction_already_revoked")
            involved = set(correction.source_asset_ids) | {correction.target_asset_id}
            later = session.scalars(
                select(ProjectCorrection).where(
                    ProjectCorrection.project_id == project_id,
                    ProjectCorrection.status == "active",
                    ProjectCorrection.id > correction.id,
                )
            )
            if any(involved & (set(c.source_asset_ids) | {c.target_asset_id}) for c in later):
                raise ConflictError("correction_has_dependents")
            project = self.projects.bump(session, project_id, expected_revision)
            members = session.scalars(
                select(ProjectCorrectionMember).where(
                    ProjectCorrectionMember.correction_id == correction_id
                )
            )
            for member in members:
                row = session.get(ProjectObservation, member.observation_id)
                if row.display_asset_id != member.next_asset_id:
                    raise ConflictError("correction_has_dependents")
                row.display_asset_id = member.previous_asset_id
            correction.status = "revoked"
            session.add(
                ProjectAudit(
                    project_id=project_id,
                    action="revoke",
                    detail={
                        "correction_id": correction.id,
                        "revision": project.revision,
                        "reason": safe_text(reason, limit=2000),
                    },
                )
            )
            session.flush()
            return correction

    def project_new_observations(self, session, project_run_id):
        rows = list(
            session.scalars(
                select(ProjectObservation).where(
                    ProjectObservation.project_run_id == project_run_id
                )
            )
        )
        if not rows:
            return
        corrections = list(
            session.scalars(
                select(ProjectCorrection)
                .where(
                    ProjectCorrection.project_id == rows[0].project_id,
                    ProjectCorrection.status == "active",
                    ProjectCorrection.action == "merge",
                )
                .order_by(ProjectCorrection.id)
            )
        )
        for row in rows:
            for correction in corrections:
                if row.display_asset_id in correction.source_asset_ids:
                    session.add(
                        ProjectCorrectionMember(
                            correction_id=correction.id,
                            observation_id=row.id,
                            previous_asset_id=row.display_asset_id,
                            next_asset_id=correction.target_asset_id,
                        )
                    )
                    row.display_asset_id = correction.target_asset_id
        session.flush()

    def annotate(self, session, project_id, asset_id, tags, note, expected_revision):
        if (
            len(tags) > 20
            or any(not isinstance(t, str) or not t.strip() or len(t) > 40 for t in tags)
            or len(note) > 2000
        ):
            raise InputValidationError("invalid_project_annotation")
        with atomic_import(session):
            asset = session.get(ProjectAsset, asset_id)
            if not asset or asset.project_id != project_id:
                raise ResourceNotFoundError("project_asset_not_found")
            project = self.projects.bump(session, project_id, expected_revision)
            annotation = session.get(ProjectAnnotation, asset_id)
            if annotation is None:
                annotation = ProjectAnnotation(asset_id=asset_id)
                session.add(annotation)
            annotation.tags = list(dict.fromkeys(safe_text(t, limit=40) for t in tags))
            annotation.note = safe_text(note, limit=2000) or ""
            session.add(
                ProjectAudit(
                    project_id=project_id,
                    action="annotate",
                    detail={
                        "asset_id": asset_id,
                        "revision": project.revision,
                        "tags": annotation.tags,
                        "note": annotation.note,
                    },
                )
            )
            session.flush()
            return annotation

    def review(self, session, project_id, request):
        """Only the explicitly selected IDs are changed, in one project revision."""
        with atomic_import(session):
            ids = list(dict.fromkeys(request.asset_ids))
            assets = list(
                session.scalars(
                    select(ProjectAsset).where(
                        ProjectAsset.project_id == project_id, ProjectAsset.id.in_(ids)
                    )
                )
            )
            if len(assets) != len(ids):
                raise ResourceNotFoundError("project_asset_not_found")
            if not request.reason.strip():
                raise InputValidationError("invalid_review_reason")
            project = self.projects.bump(session, project_id, request.expected_revision)
            before = {}
            now = datetime.now(timezone.utc)
            for asset in assets:
                row = session.get(ProjectAnnotation, asset.id)
                if row is None:
                    row = ProjectAnnotation(asset_id=asset.id)
                    session.add(row)
                    session.flush()
                before[asset.id] = dict(
                    state=row.review_state,
                    reason=row.review_reason,
                    at=row.reviewed_at.isoformat() if row.reviewed_at else None,
                    needs_review=row.needs_review,
                    revision=row.review_revision,
                )
                row.review_state = request.state
                row.review_reason = safe_text(request.reason, limit=2000)
                row.reviewed_at = now
                row.review_revision = project.revision
                row.needs_review = False
            audit = ProjectAudit(
                project_id=project_id,
                action="review",
                detail=dict(
                    before=before,
                    state=request.state,
                    reason=safe_text(request.reason, limit=2000),
                    revision=project.revision,
                    at=now.isoformat(),
                    asset_ids=ids,
                ),
            )
            session.add(audit)
            session.flush()
            return audit

    def undo_review(self, session, project_id, audit_id, expected_revision, reason):
        with atomic_import(session):
            audit = session.get(ProjectAudit, audit_id)
            if not audit or audit.project_id != project_id or audit.action != "review":
                raise ResourceNotFoundError("project_review_missing")
            if not reason.strip() or len(reason) > 2000:
                raise InputValidationError("invalid_review_reason")
            rows = [session.get(ProjectAnnotation, aid) for aid in audit.detail["asset_ids"]]
            if any(r.review_revision != audit.detail["revision"] for r in rows):
                raise ConflictError("review_has_later_changes")
            project = self.projects.bump(session, project_id, expected_revision)
            for row in rows:
                old = audit.detail["before"][row.asset_id]
                row.review_state, row.review_reason = old["state"], old["reason"]
                row.reviewed_at = datetime.fromisoformat(old["at"]) if old["at"] else None
                row.needs_review = row.needs_review or old["needs_review"]
                row.review_revision = old["revision"]
            session.add(
                ProjectAudit(
                    project_id=project_id,
                    action="review_undo",
                    detail=dict(
                        review_id=audit.id,
                        revision=project.revision,
                        asset_ids=audit.detail["asset_ids"],
                        reason=safe_text(reason, limit=2000),
                    ),
                )
            )
            session.flush()

    def review_history(self, session, project_id, asset_id):
        ids = func.json_each(ProjectAudit.detail["asset_ids"]).table_valued("value")
        audits = list(
            session.scalars(
                select(ProjectAudit)
                .where(
                    ProjectAudit.project_id == project_id,
                    ProjectAudit.action.in_(["review", "review_undo"]),
                    select(1).select_from(ids).where(ids.c.value == asset_id).exists(),
                )
                .order_by(ProjectAudit.id.desc())
            )
        )
        selected = {aid for a in audits for aid in a.detail["asset_ids"]}
        revisions = {
            r.asset_id: r.review_revision
            for r in session.scalars(
                select(ProjectAnnotation).where(ProjectAnnotation.asset_id.in_(selected))
            )
        }
        return [
            dict(
                id=a.id,
                action=a.action,
                detail=a.detail,
                at=a.created_at,
                undoable=a.action == "review"
                and all(revisions[aid] == a.detail["revision"] for aid in a.detail["asset_ids"]),
            )
            for a in audits
        ]

    def flag_observation_changes(self, session, run):
        from collections import defaultdict

        from sqlalchemy.orm import aliased

        from app.models.project import ProjectRun
        from app.services.project_comparison import ProjectComparisonService

        reviewed = (
            select(ProjectAnnotation.asset_id)
            .join(ProjectAsset, ProjectAsset.id == ProjectAnnotation.asset_id)
            .where(
                ProjectAsset.project_id == run.project_id,
                ProjectAnnotation.reviewed_at.is_not(None),
            )
        )
        if session.scalar(reviewed.limit(1)) is None:
            return
        old, current = aliased(ProjectObservation), aliased(ProjectObservation)
        ranked = (
            select(
                old.asset_id,
                old.identity_key,
                old.project_run_id,
                func.dense_rank()
                .over(partition_by=[old.asset_id, old.identity_key], order_by=old.seen_at.desc())
                .label("position"),
            )
            .join(ProjectRun, ProjectRun.id == old.project_run_id)
            .join(
                current,
                (current.asset_id == old.asset_id) & (current.identity_key == old.identity_key),
            )
            .where(
                current.project_run_id == run.id,
                old.seen_at < current.seen_at,
                ProjectRun.entry_id == run.entry_id,
                ProjectRun.source == run.source,
                ProjectRun.status == "complete",
            )
            .subquery()
        )
        pairs = defaultdict(set)
        for aid, identity, rid in session.execute(
            select(ranked.c.asset_id, ranked.c.identity_key, ranked.c.project_run_id).where(
                ranked.c.position == 1
            )
        ):
            pairs[rid].add((aid, identity))
        changed = set()
        for rid, keys in pairs.items():
            comparison = ProjectComparisonService().compare(session, run.project_id, rid, run.id)
            changed.update(
                i["asset_id"]
                for i in comparison.items
                if (i["asset_id"], i["identity_key"]) in keys and i["observed_attributes_differ"]
            )
        if not changed:
            return
        display_ids = select(ProjectObservation.display_asset_id).where(
            ProjectObservation.project_run_id == run.id, ProjectObservation.asset_id.in_(changed)
        )
        for row in session.scalars(
            select(ProjectAnnotation).where(ProjectAnnotation.asset_id.in_(display_ids))
        ):
            row.needs_review = True
        session.flush()
