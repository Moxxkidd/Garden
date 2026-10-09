import pytest

from app.core.errors import ConflictError, InputValidationError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.projects import ProjectEntryCreate
from app.services.projects import ProjectService


def test_entry_binding_is_not_network_authorization(seeded_records):
    service = ProjectService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        target.base_url = "http://localhost:8111/app"
        p = service.create(session, "App", "Project")
        e = service.add_entry(
            session,
            p.id,
            ProjectEntryCreate(
                target_id=target.id, entry_url="http://localhost:8111/app", name="Main"
            ),
        )
        assert service.get_entry(session, p.id, e.id).origin == "http://localhost:8111"
        with pytest.raises(InputValidationError):
            service.add_entry(
                session,
                p.id,
                ProjectEntryCreate(
                    target_id=target.id, entry_url="http://other.test/app", name="Wrong"
                ),
            )
        target.base_url = "http://localhost:8222/app"
        with pytest.raises(ConflictError, match="entry_binding_changed"):
            service.validate_entry(session, p.id, e.id)
        service.archive(session, p.id, p.revision)
        with pytest.raises(ConflictError, match="project_archived"):
            service.add_entry(
                session,
                p.id,
                ProjectEntryCreate(target_id=target.id, entry_url=target.base_url, name="Main"),
            )
