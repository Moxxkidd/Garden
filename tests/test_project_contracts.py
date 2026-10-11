import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.bootstrap import upgrade_database
from app.schemas.assets import AssetRecord
from app.services.project_identity import project_asset_key
from tests.test_migrations import downgrade_database


def record(**changes):
    values = dict(
        asset_id="scan:1:asset:1",
        record_id=1,
        record_type="scan_asset",
        kind="page",
        original_type="page",
        url="https://example.test/items?id=x",
        site="https://example.test",
        method="GET",
        status_codes=[200],
        observation="response_observed",
        context="anonymous",
        provenance_note="fixture",
        source_url="/scans/1",
    )
    return AssetRecord(**(values | changes))


def test_project_route_identity_is_stable_without_losing_boundaries():
    base = record()
    assert project_asset_key(base) == project_asset_key(
        record(asset_id="scan:2:asset:4", record_id=4)
    )
    for changes in [
        dict(site="https://other.test", url="https://other.test/items?id=x"),
        dict(method="POST"),
        dict(url="https://example.test/Items?id=x"),
        dict(url="https://example.test/items/?id=x"),
        dict(url="https://example.test/items?id=x&id=y"),
        dict(route_url="https://example.test/items#/users"),
    ]:
        assert project_asset_key(base) != project_asset_key(record(**changes))
    assert project_asset_key(record(method=None)) != project_asset_key(
        record(method=None, asset_id="scan:2:asset:1")
    )
    assert project_asset_key(record(site=None)) != project_asset_key(
        record(site=None, asset_id="scan:2:asset:1")
    )


def test_0009_preserves_existing_database_and_refuses_loss(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'upgrade.db'}"
    upgrade_database(url, "0008")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO targets "
                "(id,name,base_url,type,owner,tags,status,created_at,updated_at) "
                "VALUES (42,'old','http://localhost','web','owner','[]','active',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
    upgrade_database(url, "0009")
    assert "project_observations" in inspect(engine).get_table_names()
    with engine.connect() as db:
        assert db.scalar(text("SELECT id FROM targets")) == 42
    downgrade_database(url, "0008")
    upgrade_database(url, "0009")
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO projects (name,description,status,revision,created_at,updated_at) "
                "VALUES ('project','','active',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
    with pytest.raises(RuntimeError, match="project"):
        downgrade_database(url, "0008")


def test_0009_refuses_loss_of_private_entry_proof_without_projects(tmp_path):
    from sqlalchemy.orm import Session

    from app.models.scan_run import ScanRun

    url = f"sqlite+pysqlite:///{tmp_path / 'entry-proof.db'}"
    upgrade_database(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(
            ScanRun(
                input_url="https://example.test/",
                normalized_url="https://example.test/",
                entry_url_digest="a" * 64,
            )
        )
        session.commit()
    with pytest.raises(RuntimeError, match="private project entry"):
        downgrade_database(url, "0008")
