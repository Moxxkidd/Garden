from pathlib import Path

import pytest

from app.db.bootstrap import get_session
from app.models.coverage_difference import CoverageDifference
from app.services.scan_application import create_assessment_stages
from app.services.scan_reporting import ScanReportService
from tests.helpers.assessment import add_asset, make_authenticated_run


@pytest.fixture
def db_session():
    session = get_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _reported_run(db_session):
    run = make_authenticated_run(db_session)
    run.input_url = "http://127.0.0.1:8080/"
    run.normalized_url = run.input_url
    run.status = "completed"
    run.current_stage = "finished"
    run.progress = 100
    run.completeness = "complete"
    run.options = {"max_pages": 50, "max_resources": 200, "max_depth": 2}
    for context in run.contexts:
        context.status = "completed"
        context.collection_status = "completed"
        context.completeness = "complete"
    create_assessment_stages(db_session, run)
    db_session.flush()
    db_session.expire(run, ["stages"])
    replay_stage = next(stage for stage in run.stages if stage.name == "replay_authorization")
    replay_stage.status = "skipped"
    replay_stage.summary = "v0.3 仅执行被动认证覆盖；主动权限重放未执行。"
    db_session.add(
        CoverageDifference(
            scan_run_id=run.id,
            identity_key="GET:/admin",
            classification="admin_only",
            anonymous_state="absent",
            user_state="absent",
            admin_state="present",
            anonymous_present=False,
            user_present=False,
            admin_present=True,
            context_summaries={
                "admin": {
                    "asset_id": 7,
                    "status_code": 200,
                    "url": "http://127.0.0.1:8080/admin",
                    "title": "Admin",
                    "content_type": "text/html",
                    "content_signature": "safe-signature",
                }
            },
            confidence="high",
            diagnostic="仅管理员上下文观察到该资产。",
        )
    )
    db_session.flush()
    return run


def test_authenticated_report_renders_context_health_and_difference_matrix(
    db_session,
    tmp_path: Path,
) -> None:
    run = _reported_run(db_session)

    path = ScanReportService(tmp_path / "reports").generate(db_session, run.id)
    text = Path(path).read_text(encoding="utf-8")

    assert text.startswith(f"# Garden 认证覆盖报告 #{run.id}")
    assert "## 三上下文健康与完整性" in text
    assert "## 覆盖差异分类计数" in text
    assert "| admin_only | 1 |" in text
    assert "| 资产身份 | anonymous | user | admin | 分类 | 置信度 |" in text
    assert "| `GET:/admin` | absent | absent | present | admin_only | high |" in text
    assert "主动权限重放未执行" in text
    assert "覆盖差异不是已确认漏洞" in text


def test_authenticated_report_preserves_unknown_and_omits_forbidden_asset_values(
    db_session,
    tmp_path: Path,
) -> None:
    run = _reported_run(db_session)
    contexts = {context.kind: context for context in run.contexts}
    contexts["user"].status = "failed"
    contexts["user"].collection_status = "failed"
    contexts["user"].completeness = "incomplete"
    contexts["user"].error_code = "authentication_session_unavailable"
    difference = run.coverage_differences[0]
    difference.classification = "unknown"
    difference.user_state = "unknown"
    difference.user_present = None
    difference.confidence = "low"
    difference.diagnostic = "至少一个上下文不完整，无法判断该资产是否缺失。"
    raw_values = [
        "Bearer report-secret",
        "session=report-secret",
        "private response report-secret",
        "vault://report-secret",
    ]
    add_asset(
        db_session,
        run,
        context_id=contexts["admin"].id,
        identity_key="GET:/private",
        attributes={
            "authorization": raw_values[0],
            "cookie": raw_values[1],
            "response_text": raw_values[2],
            "protected_storage_ref": raw_values[3],
        },
    )
    db_session.flush()

    path = ScanReportService(tmp_path / "reports").generate(db_session, run.id)
    text = Path(path).read_text(encoding="utf-8")

    assert "| `GET:/admin` | absent | unknown | present | unknown | low |" in text
    assert "unknown" in text
    assert "## 失败与未覆盖部分" in text
    assert "user：采集=failed，完整性=incomplete，代码=authentication_session_unavailable" in text
    assert all(value not in text for value in raw_values)


def test_authenticated_report_indexes_evidence_and_allowlists_inconsistent_details(
    db_session,
    tmp_path: Path,
) -> None:
    run = _reported_run(db_session)
    forbidden = "cookie=report-must-not-render"
    db_session.add(
        CoverageDifference(
            scan_run_id=run.id,
            identity_key="GET:/profile",
            classification="inconsistent",
            anonymous_state="present",
            user_state="present",
            admin_state="present",
            anonymous_present=True,
            user_present=True,
            admin_present=True,
            context_summaries={
                "anonymous": {
                    "asset_id": 11,
                    "status_code": 200,
                    "url": "http://127.0.0.1:8080/profile",
                    "title": "Profile",
                    "content_type": "application/json",
                    "content_signature": "anonymous-signature",
                    "cookie": forbidden,
                },
                "user": {
                    "asset_id": 12,
                    "status_code": 200,
                    "url": "http://127.0.0.1:8080/profile",
                    "title": "Profile",
                    "content_type": "application/json",
                    "content_signature": "user-signature",
                },
                "admin": {
                    "asset_id": 13,
                    "status_code": 200,
                    "url": "http://127.0.0.1:8080/profile",
                    "title": "Profile",
                    "content_type": "application/json",
                    "content_signature": "admin-signature",
                },
            },
            confidence="medium",
            diagnostic="三个上下文的稳定响应属性不一致。",
        )
    )
    db_session.flush()

    path = ScanReportService(tmp_path / "reports").generate(db_session, run.id)
    text = Path(path).read_text(encoding="utf-8")

    assert "## 属性不一致详情" in text
    assert "| anonymous | 200 | http://127.0.0.1:8080/profile |" in text
    assert "## 被动观察与证据索引" in text
    assert "| `GET:/profile` | A11 | A12 | A13 |" in text
    assert forbidden not in text
