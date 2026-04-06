from datetime import datetime, timezone

from app.db.bootstrap import session_scope
from app.models.credential_profile import CredentialProfile
from app.models.scan_job import ScanJob
from app.models.target import Target


def test_model_persistence_and_relationships() -> None:
    with session_scope() as session:
        target = Target(
            name="model-demo",
            base_url="http://localhost:9200",
            type="web",
            owner="platform",
            tags=["demo", "local"],
            status="active",
        )
        session.add(target)
        session.flush()

        credential = CredentialProfile(
            target_id=target.id,
            name="model-admin",
            role="administrator",
            auth_type="password",
            username="model@example.local",
            secret_ref="vault://garden/model-admin",
            login_config_path="examples/login/model.yaml",
        )
        session.add(credential)
        session.flush()

        job = ScanJob(
            target_id=target.id,
            credential_profile_id=credential.id,
            status="completed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            summary="Model relationship check",
            error_message=None,
        )
        session.add(job)
        session.flush()

        assert target.id == 1
        assert credential.target_id == target.id
        assert job.credential_profile_id == credential.id
        assert target.created_at is not None
        assert credential.updated_at is not None
