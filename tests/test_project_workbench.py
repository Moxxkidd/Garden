"""Workbench behavior at the existing project application boundary."""

import pytest

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectQuery, ProjectReviewRequest
from app.services.project_catalog import ProjectCatalogService
from app.services.project_curation import ProjectCurationService
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_explicit_bulk_review_undo_and_changed_observation_preserve_notes(seeded_records):
    from app.core.errors import ConflictError, ResourceNotFoundError

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for path in ("/item/1", "/item/2", "/untouched"):
            ingest(session, project, entry, source_run(session, target, path=path))
        catalog, curation = ProjectCatalogService(), ProjectCurationService()
        page = catalog.list(session, project.id, ProjectQuery(sort="url"))
        ids = [row["id"] for row in page.items[:2]]
        curation.annotate(session, project.id, ids[0], ["reviewed"], "old note", page.revision)
        assert catalog.reviews(session, project.id, ids)[ids[0]]["state"] == "unreviewed"
        request = ProjectReviewRequest(
            asset_ids=ids, state="keep", reason="Core flow", expected_revision=project.revision
        )
        action = curation.review(session, project.id, request)
        reviewed = catalog.reviews(session, project.id, ids)
        assert {row["state"] for row in reviewed.values()} == {"keep"}
        assert catalog.list(session, project.id, ProjectQuery(collection="keep")).matched == 2
        with pytest.raises(ConflictError):
            curation.review(session, project.id, request)
        curation.undo_review(session, project.id, action.id, project.revision, "undo selection")
        assert catalog.list(session, project.id, ProjectQuery(collection="keep")).matched == 0
        action = curation.review(
            session, project.id, request.model_copy(update={"expected_revision": project.revision})
        )
        ingest(
            session,
            project,
            entry,
            source_run(session, target, path="/item/1", day=2, status_code=403),
        )
        reviewed = catalog.reviews(session, project.id, ids)
        assert reviewed[ids[0]]["state"] == "keep"
        assert reviewed[ids[0]]["needs_review"] is True
        assert reviewed[ids[1]]["needs_review"] is False
        assert catalog.detail(session, project.id, ids[0]).annotation == {
            "tags": ["reviewed"],
            "note": "old note",
        }
        assert catalog.list(session, project.id, ProjectQuery()).matched == 3
        with pytest.raises(ResourceNotFoundError):
            curation.review(
                session,
                project.id,
                request.model_copy(
                    update={"asset_ids": [ids[0], "f" * 32], "expected_revision": project.revision}
                ),
            )
        assert catalog.reviews(session, project.id, ids)[ids[0]]["state"] == "keep"


def test_template_projection_and_saved_query_preserve_instances_and_identity(seeded_records):
    from app.schemas.projects import ProjectSavedViewRequest
    from app.services.project_workbench import ProjectWorkbenchService

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        for path, profile in [
            ("/orders/12?lang=en", None),
            ("/orders/34?lang=zh", None),
            ("/orders/12?lang=en", seeded_records["credential"].id),
            ("/orders/new?lang=en", None),
        ]:
            ingest(
                session,
                project,
                entry,
                source_run(
                    session,
                    target,
                    path=path,
                    profile_id=profile,
                    status_code=403 if profile else 200,
                ),
            )
        from app.models.scan_run import ScanAsset

        post = source_run(session, target, path="/orders/12?lang=en")
        session.query(ScanAsset).filter(ScanAsset.scan_run_id == post.id).update({"method": "POST"})
        session.flush()
        ingest(session, project, entry, post)
        from app.schemas.projects import ProjectEntryCreate
        from app.services.projects import ProjectService

        other = Target(
            name="other-site",
            base_url="http://localhost:8112/",
            type="web",
            owner="fixture",
            tags=[],
            status="active",
        )
        session.add(other)
        session.flush()
        other_entry = ProjectService().add_entry(
            session,
            project.id,
            ProjectEntryCreate(target_id=other.id, entry_url=other.base_url, name="other"),
        )
        ingest(session, project, other_entry, source_run(session, other, path="/orders/12?lang=en"))
        catalog, workbench = ProjectCatalogService(), ProjectWorkbenchService()
        original = catalog.list(session, project.id, ProjectQuery())
        grouped = workbench.templates(session, project.id, ProjectQuery())
        assert grouped.matched == 4
        group = next(g for g in grouped.items if g["instance_count"] == 2)
        assert group["path"] == "/orders/{number}"
        assert group["site"] == "http://localhost:8111"
        assert group["method"] == "GET"
        assert group["status_codes"] == [200, 403]
        assert group["parameters"] == ["lang"]
        assert len(group["identities"]) == 2
        expanded = catalog.list(session, project.id, ProjectQuery(template_key=group["key"]))
        assert expanded.matched == 2
        assert sum(a["observation_count"] for a in expanded.items) == 3
        saved = workbench.save_view(
            session,
            project.id,
            ProjectSavedViewRequest(
                name="Core orders",
                query=ProjectQuery(q="orders", collection="core", page=2),
                expected_revision=project.revision,
            ),
        )
        restored = workbench.views(session, project.id)[0]
        assert restored["id"] == saved.id
        assert restored["query"]["page"] == 1
        assert catalog.list(session, project.id, ProjectQuery(**restored["query"])).matched == 5
        assert {a["id"] for a in catalog.list(session, project.id, ProjectQuery()).items} == {
            a["id"] for a in original.items
        }
