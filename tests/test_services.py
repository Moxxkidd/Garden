import pytest

from app.core.errors import ConflictError
from app.db.bootstrap import session_scope
from app.models.enums import AuthType, JobStatus, TargetStatus, TargetType
from app.schemas.credential import CredentialProfileCreate
from app.schemas.job import ScanJobCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.jobs import ScanJobService
from app.services.targets import TargetService


def test_target_service_crud() -> None:
    service = TargetService()
    with session_scope() as session:
        target = service.create(
            session,
            TargetCreate(
                name="service-target",
                base_url="http://localhost:9300",
                type=TargetType.API,
                owner="service-team",
                tags=["api", "local"],
                status=TargetStatus.ACTIVE,
            ),
        )
        fetched = service.get(session, target.id)
        assert fetched.name == "service-target"
        assert len(service.list(session)) == 1

    with session_scope() as session:
        service.delete(session, target.id)
        assert service.list(session) == []


def test_credential_service_crud() -> None:
    target_service = TargetService()
    credential_service = CredentialProfileService()
    with session_scope() as session:
        target = target_service.create(
            session,
            TargetCreate(
                name="credential-target",
                base_url="http://localhost:9301",
                type=TargetType.WEB,
                owner="service-team",
                tags=[],
                status=TargetStatus.ACTIVE,
            ),
        )
        credential = credential_service.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="service-credential",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="svc@example.local",
                secret_ref="vault://garden/service",
                login_config_path="examples/login/service.yaml",
            ),
        )
        assert credential_service.get(session, credential.id).name == "service-credential"
        assert len(credential_service.list(session)) == 1

    with session_scope() as session:
        credential_service.delete(session, credential.id)
        assert credential_service.list(session) == []


def test_job_service_create_and_fetch() -> None:
    target_service = TargetService()
    credential_service = CredentialProfileService()
    job_service = ScanJobService()
    with session_scope() as session:
        target = target_service.create(
            session,
            TargetCreate(
                name="job-target",
                base_url="http://localhost:9302",
                type=TargetType.ADMIN,
                owner="service-team",
                tags=[],
                status=TargetStatus.ACTIVE,
            ),
        )
        credential = credential_service.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="job-credential",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="job@example.local",
                secret_ref="vault://garden/job",
                login_config_path="examples/login/job.yaml",
            ),
        )
        job = job_service.create(
            session,
            ScanJobCreate(
                target_id=target.id,
                credential_profile_id=credential.id,
                status=JobStatus.QUEUED,
                summary="Queued job",
            ),
        )
        assert job_service.get(session, job.id).summary == "Queued job"


def test_target_delete_blocked_when_related_records_exist() -> None:
    target_service = TargetService()
    credential_service = CredentialProfileService()
    with session_scope() as session:
        target = target_service.create(
            session,
            TargetCreate(
                name="blocked-target",
                base_url="http://localhost:9303",
                type=TargetType.WEB,
                owner="service-team",
                tags=[],
                status=TargetStatus.ACTIVE,
            ),
        )
        credential_service.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name="blocked-credential",
                role="admin",
                auth_type=AuthType.PASSWORD,
                username="blocked@example.local",
                secret_ref="vault://garden/blocked",
                login_config_path="examples/login/blocked.yaml",
            ),
        )
        with pytest.raises(ConflictError):
            target_service.delete(session, target.id)
