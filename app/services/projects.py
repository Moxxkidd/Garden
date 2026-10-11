"""Project membership is an organizational boundary, not network authorization."""

from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, update

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.models.project import Project, ProjectEntry
from app.models.target import Target
from app.schemas.projects import ProjectEntryCreate
from app.services.asset_catalog import safe_text


def project_url(value):
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError
        port = p.port
        host = p.hostname.lower()
        host = f"[{host}]" if ":" in host else host
        authority = host + (
            f":{port}" if port and port != (443 if p.scheme == "https" else 80) else ""
        )
        return urlunsplit((p.scheme, authority, p.path or "/", p.query, p.fragment))
    except (ValueError, AttributeError):
        raise InputValidationError("invalid_project_url") from None


def project_origin(value):
    p = urlsplit(project_url(value))
    return f"{p.scheme}://{p.netloc}"


class ProjectService:
    def get(self, session, project_id):
        project = session.get(Project, project_id)
        if project is None:
            raise ResourceNotFoundError("project_not_found")
        return project

    def list(self, session):
        return list(session.scalars(select(Project).order_by(Project.id)))

    def writable(self, session, project_id):
        project = self.get(session, project_id)
        if project.status != "active":
            raise ConflictError("project_archived")
        return project

    def bump(self, session, project_id, expected_revision=None):
        project = self.writable(session, project_id)
        expected = project.revision if expected_revision is None else expected_revision
        result = session.execute(
            update(Project)
            .where(
                Project.id == project_id, Project.revision == expected, Project.status == "active"
            )
            .values(revision=expected + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ConflictError("project_revision_changed")
        session.refresh(project)
        return project

    def create(self, session, name, description=""):
        if not name.strip() or len(name) > 120 or len(description) > 2000:
            raise InputValidationError("invalid_project_metadata")
        project = Project(
            name=safe_text(name), description=safe_text(description, limit=2000) or ""
        )
        session.add(project)
        session.flush()
        return project

    def archive(self, session, project_id, expected_revision):
        project = self.bump(session, project_id, expected_revision)
        project.status = "archived"
        session.flush()
        return project

    def get_entry(self, session, project_id, entry_id):
        self.get(session, project_id)
        entry = session.get(ProjectEntry, entry_id)
        if not entry or entry.project_id != project_id:
            raise ResourceNotFoundError("project_entry_not_found")
        return entry

    def entries(self, session, project_id):
        self.get(session, project_id)
        return list(
            session.scalars(
                select(ProjectEntry)
                .where(ProjectEntry.project_id == project_id)
                .order_by(ProjectEntry.id)
            )
        )

    def _entry_values(self, session, request: ProjectEntryCreate):
        target = session.get(Target, request.target_id)
        if not target or target.status != "active":
            raise InputValidationError("target_unavailable")
        url = project_url(request.entry_url)
        if project_origin(url) != project_origin(target.base_url):
            raise InputValidationError("entry_origin_mismatch")
        return dict(
            target_id=target.id,
            entry_url=url,
            origin=project_origin(url),
            target_base_url=target.base_url,
            name=safe_text(request.name),
            enabled=request.enabled,
        )

    def add_entry(self, session, project_id, request):
        self.writable(session, project_id)
        values = self._entry_values(session, request)
        self.bump(session, project_id)
        entry = ProjectEntry(project_id=project_id, **values)
        session.add(entry)
        session.flush()
        return entry

    def update_entry(self, session, entry_id, request, expected_revision):
        entry = session.get(ProjectEntry, entry_id)
        if not entry:
            raise ResourceNotFoundError("project_entry_not_found")
        values = self._entry_values(session, request)
        self.bump(session, entry.project_id, expected_revision)
        for key, value in values.items():
            setattr(entry, key, value)
        session.flush()
        return entry

    def validate_entry(self, session, project_id, entry_id):
        self.writable(session, project_id)
        entry = self.get_entry(session, project_id, entry_id)
        target = session.get(Target, entry.target_id)
        if (
            not entry.enabled
            or not target
            or target.status != "active"
            or target.base_url != entry.target_base_url
        ):
            raise ConflictError("entry_binding_changed")
        if project_origin(target.base_url) != entry.origin:
            raise ConflictError("entry_binding_changed")
        return entry
