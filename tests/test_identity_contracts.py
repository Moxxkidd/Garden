import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.db.bootstrap import session_scope
from app.models.scan_context import ScanContext
from app.schemas.identity import IdentityCollectionRequest, ManualLoginRequest
from tests.helpers.assessment import make_run


def test_single_profile_and_anonymous_only():
    assert IdentityCollectionRequest(
        url="https://site.test", target_id=1, profile_ids=[7]
    ).profile_ids == [7]
    assert (
        IdentityCollectionRequest(
            url="https://site.test", target_id=1, include_anonymous=True
        ).profile_ids
        == []
    )


@pytest.mark.parametrize(
    "profiles,anonymous", [([], False), ([7, 7], False), (list(range(1, 12)), False), ([0], False)]
)
def test_invalid_identity_selection(profiles, anonymous):
    with pytest.raises(ValidationError):
        IdentityCollectionRequest(
            url="https://site.test", target_id=1, profile_ids=profiles, include_anonymous=anonymous
        )


def test_manual_login_requires_positive_condition():
    with pytest.raises(ValidationError):
        ManualLoginRequest(
            profile_id=7, login_url="https://site.test/login", validate_url="https://site.test/me"
        )


def test_context_keys_allow_distinct_identities_and_preserve_old_uniqueness():
    with session_scope() as session:
        run = make_run(session, mode="identity_collection")
        session.add_all(
            [
                ScanContext(scan_run_id=run.id, kind="identity", context_key=f"profile:{n}")
                for n in [7, 8]
            ]
        )
        session.flush()
        assert len(run.contexts) == 2
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(ScanContext(scan_run_id=run.id, kind="identity", context_key="profile:7"))
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add_all(
                [
                    ScanContext(scan_run_id=run.id, kind="user", context_key=k)
                    for k in ["user", "other"]
                ]
            )
            session.flush()


def test_0008_preserves_old_context_and_refuses_loss(tmp_path):
    from sqlalchemy import create_engine, text

    from app.db.bootstrap import upgrade_database
    from tests.test_migrations import downgrade_database

    url = f"sqlite+pysqlite:///{tmp_path / 'identities.db'}"
    upgrade_database(url, "0007")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text("""INSERT INTO scan_runs
        (id,input_url,normalized_url,status,current_stage,progress,options,retry_count,created_at,updated_at)
        VALUES (1,'https://site.test/','https://site.test/','completed','report',100,'{}',0,
                CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO scan_contexts
        (id,scan_run_id,kind,created_at,updated_at) VALUES (7,1,'user',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
    upgrade_database(url)
    with engine.connect() as db:
        assert db.execute(text("SELECT id,context_key FROM scan_contexts")).one() == (7, "user")
        assert not db.execute(text("PRAGMA foreign_key_check")).all()
    downgrade_database(url, "0007")
    upgrade_database(url)
    with engine.begin() as db:
        db.execute(
            text("UPDATE scan_contexts SET kind='identity',context_key='profile:7' WHERE id=7")
        )
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        downgrade_database(url, "0007")
