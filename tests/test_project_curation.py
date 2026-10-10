from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectCorrectionRequest, ProjectQuery
from app.services.project_catalog import ProjectCatalogService
from app.services.project_curation import ProjectCurationService
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_merge_split_and_revoke_keep_original_history(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for path in ("/first", "/second"):
            ingest(session, project, entry, source_run(session, target, path=path))
        catalog, curation = ProjectCatalogService(), ProjectCurationService()
        before = catalog.list(session, project.id, ProjectQuery(view="observations"))
        original = [(r["id"], r["asset_id"], r["record"]) for r in before.items]
        ids = [r["asset_id"] for r in before.items]
        preview = curation.preview(
            session,
            project.id,
            ProjectCorrectionRequest(
                action="merge",
                asset_ids=ids,
                target_asset_id=ids[0],
                reason="same route by operator review",
            ),
        )
        correction = curation.confirm(session, project.id, preview.token)
        merged = catalog.list(session, project.id, ProjectQuery())
        assert merged.matched == 1
        after = catalog.list(session, project.id, ProjectQuery(view="observations"))
        assert [(r["id"], r["asset_id"], r["record"]) for r in after.items] == original
        curation.revoke(session, project.id, correction.id, merged.revision, "undo fixture")
        assert catalog.list(session, project.id, ProjectQuery()).matched == 2


def test_split_only_selected_observations_and_future_import_keeps_natural_subject(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for day in (1, 2):
            ingest(session, project, entry, source_run(session, target, day=day))
        catalog, curation = ProjectCatalogService(), ProjectCurationService()
        history = catalog.list(session, project.id, ProjectQuery(view="observations"))
        original_id = history.items[0]["asset_id"]
        preview = curation.preview(
            session,
            project.id,
            ProjectCorrectionRequest(
                action="split",
                asset_ids=[original_id],
                observation_ids=[history.items[0]["id"]],
                reason="one observation belongs separately",
            ),
        )
        split = curation.confirm(session, project.id, preview.token)
        assert split.target_asset_id != original_id
        ingest(session, project, entry, source_run(session, target, day=3))
        separated = catalog.list(session, project.id, ProjectQuery())
        assert sorted(a["observation_count"] for a in separated.items) == [1, 2]
        curation.revoke(session, project.id, split.id, separated.revision, "undo split")
        merged = catalog.list(session, project.id, ProjectQuery())
        assert merged.matched == 1
        assert merged.items[0]["observation_count"] == 3


def test_merge_applies_to_new_observations_and_revoke_restores_them(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for path in ("/one", "/two"):
            ingest(session, project, entry, source_run(session, target, path=path))
        catalog, curation = ProjectCatalogService(), ProjectCurationService()
        assets = catalog.list(session, project.id, ProjectQuery(sort="url")).items
        preview = curation.preview(
            session,
            project.id,
            ProjectCorrectionRequest(
                action="merge",
                asset_ids=[a["id"] for a in assets],
                target_asset_id=assets[0]["id"],
                reason="alias",
            ),
        )
        correction = curation.confirm(session, project.id, preview.token)
        ingest(session, project, entry, source_run(session, target, path="/two", day=2))
        merged = catalog.list(session, project.id, ProjectQuery())
        assert merged.matched == 1
        assert merged.items[0]["observation_count"] == 3
        curation.revoke(session, project.id, correction.id, merged.revision, "undo")
        assert sorted(
            a["observation_count"] for a in catalog.list(session, project.id, ProjectQuery()).items
        ) == [1, 2]


def test_annotations_remain_on_original_id_after_merge(seeded_records):
    import pytest

    from app.core.errors import ConflictError, InputValidationError

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for path in ("/one", "/two"):
            ingest(session, project, entry, source_run(session, target, path=path))
        catalog, curation = ProjectCatalogService(), ProjectCurationService()
        page = catalog.list(session, project.id, ProjectQuery(sort="url"))
        one, two = [a["id"] for a in page.items]
        curation.annotate(session, project.id, two, ["reviewed"], "operator note", page.revision)
        with pytest.raises(ConflictError):
            curation.annotate(session, project.id, two, [], "stale", page.revision)
        with pytest.raises(InputValidationError):
            curation.annotate(session, project.id, two, ["x" * 41], "", page.revision + 1)
        preview = curation.preview(
            session,
            project.id,
            ProjectCorrectionRequest(
                action="merge", asset_ids=[one, two], target_asset_id=one, reason="review"
            ),
        )
        curation.confirm(session, project.id, preview.token)
        assert catalog.detail(session, project.id, two).annotation["tags"] == ["reviewed"]
        detail = catalog.detail(session, project.id, one)
        assert detail.annotation["tags"] == []
        assert two in detail.aliases


def test_stale_correction_and_dependent_revoke_are_rejected(seeded_records):
    import pytest

    from app.core.errors import ConflictError

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for path in ("/one", "/two", "/three"):
            ingest(session, project, entry, source_run(session, target, path=path))
        catalog, curation = ProjectCatalogService(), ProjectCurationService()
        ids = [a["id"] for a in catalog.list(session, project.id, ProjectQuery()).items]
        request = ProjectCorrectionRequest(
            action="merge", asset_ids=ids[:2], target_asset_id=ids[0], reason="first"
        )
        stale = curation.preview(session, project.id, request)
        ingest(session, project, entry, source_run(session, target, day=2))
        with pytest.raises(ConflictError, match="project_revision_changed"):
            curation.confirm(session, project.id, stale.token)
        first = curation.confirm(
            session, project.id, curation.preview(session, project.id, request).token
        )
        second_request = ProjectCorrectionRequest(
            action="merge", asset_ids=[ids[0], ids[2]], target_asset_id=ids[2], reason="dependent"
        )
        second = curation.confirm(
            session, project.id, curation.preview(session, project.id, second_request).token
        )
        page = catalog.list(session, project.id, ProjectQuery())
        with pytest.raises(ConflictError, match="correction_has_dependents"):
            curation.revoke(session, project.id, first.id, page.revision, "unsafe order")
        curation.revoke(session, project.id, second.id, page.revision, "reverse order")
        page = catalog.list(session, project.id, ProjectQuery())
        curation.revoke(session, project.id, first.id, page.revision, "now safe")
        assert catalog.list(session, project.id, ProjectQuery()).matched == 4
