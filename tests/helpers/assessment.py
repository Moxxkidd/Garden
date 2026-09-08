"""统一验证运行测试数据工厂。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.scan_context import ScanContext
from app.models.scan_request import ScanRequest
from app.models.scan_run import ScanAsset, ScanRun


def make_run(
    session: Session,
    *,
    mode: str = "quick",
    input_url: str = "http://127.0.0.1:8000",
) -> ScanRun:
    run = ScanRun(
        input_url=input_url,
        normalized_url=input_url,
        mode=mode,
        status="queued",
        current_stage="queued",
        options={},
    )
    session.add(run)
    session.flush()
    return run


def make_authenticated_run(
    session: Session,
    user_profile_id: int | None = None,
    admin_profile_id: int | None = None,
) -> ScanRun:
    run = make_run(session, mode="authenticated_coverage")
    session.add_all(
        [
            ScanContext(scan_run_id=run.id, kind="anonymous", status="pending"),
            ScanContext(
                scan_run_id=run.id,
                kind="user",
                credential_profile_id=user_profile_id,
                status="pending",
            ),
            ScanContext(
                scan_run_id=run.id,
                kind="admin",
                credential_profile_id=admin_profile_id,
                status="pending",
            ),
        ]
    )
    session.flush()
    return run


def add_asset(
    session: Session,
    run: ScanRun,
    *,
    url: str = "http://127.0.0.1:8000/",
    context_id: int | None = None,
    identity_key: str = "GET:/",
    status_code: int | None = None,
    title: str | None = None,
    attributes: dict[str, object] | None = None,
) -> ScanAsset:
    asset = ScanAsset(
        scan_run_id=run.id,
        context_id=context_id,
        identity_key=identity_key,
        asset_type="page",
        url=url,
        method="GET",
        status_code=status_code,
        title=title,
        attributes=attributes or {},
        discovered_at=datetime.now(timezone.utc),
    )
    session.add(asset)
    session.flush()
    return asset


def add_request(
    session: Session,
    context: ScanContext,
    *,
    normalized_redacted_url: str = "http://127.0.0.1:8000/items?token=[REDACTED]",
    fingerprint: str = "request-fingerprint",
    protected_storage_ref: str = "vault://assessment/request/1",
) -> ScanRequest:
    request = ScanRequest(
        scan_run_id=context.scan_run_id,
        source_context_id=context.id,
        method="GET",
        normalized_redacted_url=normalized_redacted_url,
        fingerprint=fingerprint,
        protected_storage_ref=protected_storage_ref,
    )
    session.add(request)
    session.flush()
    return request
