"""统一上下文采集和敏感请求持久化行为测试。"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.bootstrap import get_session
from app.integrations.inventory.playwright_gateway import (
    InventoryCollectionResult,
    ObservedEndpoint,
    ObservedPage,
)
from app.models.scan_request import ScanRequest
from app.models.scan_run import ScanAsset, ScanRunStage
from app.schemas.scan import FetchResult, ScanOptions
from app.services.context_collection import (
    ContextCollectionService,
    DefaultContextCollectionGateway,
    ObservedRequest,
    ObservedResource,
)
from app.services.scan_pipeline import ScanPipeline
from app.services.session_storage import SessionStorageService
from tests.helpers.assessment import make_authenticated_run


@pytest.fixture
def db_session():
    session = get_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class FakeCollectionGateway:
    def collect(self, run, context):
        resources = [
            ObservedResource(
                asset_type="page",
                method="GET",
                url="https://app.example/admin/?nonce=first",
                status_code=200,
                title="Admin",
                attributes={
                    "content_type": "text/html",
                    "response_text": "generated=2026-08-11T01:02:03Z stable=admin",
                },
            ),
            ObservedResource(
                asset_type="endpoint",
                method="GET",
                url="https://app.example/api/users?id=1&sort=name",
                status_code=200,
                title=None,
                attributes={
                    "content_type": "application/json",
                    "response_text": '{"traceId":"req-1","stable":"users"}',
                },
            ),
            ObservedResource(
                asset_type="endpoint",
                method="GET",
                url="https://app.example/api/users?sort=date&id=9",
                status_code=200,
                title=None,
                attributes={
                    "content_type": "application/json",
                    "response_text": '{"traceId":"req-2","stable":"users"}',
                },
            ),
        ]
        requests = [
            ObservedRequest(
                method="GET",
                url="https://app.example/api/users?id=top-secret&sort=name",
                headers={
                    "accept": "application/json",
                    "authorization": "Bearer source-admin-secret",
                    "cookie": "session=source-cookie-secret",
                },
                body=None,
                status_code=200,
                response_headers={"content-type": "application/json"},
                response_text='{ "traceId": "req-1", "stable": "users" }',
            )
        ]
        return resources, requests


class FakeHttpGateway:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def fetch(self, url, options):
        self.urls.append(url)
        if url.endswith("/start"):
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                content_type="text/html",
                body_text="<title>Start</title>",
                body_size_bytes=20,
                title="Start",
                discovered_urls=[
                    "http://127.0.0.1:8080/next?id=1",
                    "http://outside.example/ignored",
                ],
            )
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": "application/json"},
            content_type="application/json",
            body_text='{"stable":"next"}',
            body_size_bytes=17,
        )


class FakeInventoryService:
    def __init__(self) -> None:
        self.calls = []

    def collect(self, target, auth_session, controls):
        self.calls.append((target, auth_session, controls))
        return InventoryCollectionResult(
            start_url=target.base_url,
            pages=[
                ObservedPage(
                    url=f"{target.base_url.rstrip('/')}/home",
                    title="Home",
                    visited_at=admin_timestamp(),
                    depth=0,
                    discovered_urls=[],
                    status_code=200,
                    text_preview="stable home",
                )
            ],
            endpoints=[
                ObservedEndpoint(
                    method="GET",
                    url=f"{target.base_url.rstrip('/')}/api/users",
                    request_url=f"{target.base_url.rstrip('/')}/api/users?id=42",
                    path="/api/users",
                    status_code=200,
                    observed_at=admin_timestamp(),
                    content_type="application/json",
                    response_preview='{"stable":"users"}',
                    request_headers={"authorization": "Bearer exact-source-token"},
                    request_body=None,
                    response_headers={"content-type": "application/json"},
                )
            ],
        )


def admin_timestamp():
    return datetime.now(timezone.utc)


class FailingOneContextCollectionService:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    def collect(self, session, run, context):
        self.kinds.append(context.kind)
        if context.kind == "user":
            raise RuntimeError("raw-user-collection-secret")
        context.status = "completed"
        context.collection_status = "completed"
        context.completeness = "complete"
        context.asset_count = 1
        return SimpleNamespace(
            context_id=context.id,
            asset_count=1,
            request_count=0,
            failure_count=0,
        )


def test_collection_persists_context_scoped_assets_and_protected_requests(
    db_session,
    tmp_path,
) -> None:
    run = make_authenticated_run(db_session)
    admin = next(context for context in run.contexts if context.kind == "admin")
    admin.status = "ready"
    storage = SessionStorageService(tmp_path / "protected-captures")

    summary = ContextCollectionService(
        gateway=FakeCollectionGateway(),
        storage_service=storage,
    ).collect(db_session, run, admin)
    db_session.flush()

    assert summary.asset_count == 2
    assert summary.request_count == 1
    assert summary.failure_count == 0
    assets = list(
        db_session.scalars(
            select(ScanAsset)
            .where(ScanAsset.scan_run_id == run.id, ScanAsset.context_id == admin.id)
            .order_by(ScanAsset.id)
        )
    )
    assert all(asset.context_id == admin.id for asset in assets)
    assert len({asset.identity_key for asset in assets}) == 2
    assert all("response_text" not in asset.attributes for asset in assets)
    requests = list(
        db_session.scalars(select(ScanRequest).where(ScanRequest.source_context_id == admin.id))
    )
    assert {request.method for request in requests} == {"GET"}
    assert requests[0].asset_id == next(
        asset.id for asset in assets if asset.asset_type == "endpoint"
    )
    assert requests[0].normalized_redacted_url.endswith("/api/users?id=[REDACTED]&sort=[REDACTED]")
    assert requests[0].protected_storage_ref
    protected_payload = storage.read_payload(requests[0].protected_storage_ref)
    assert stat.S_IMODE(Path(requests[0].protected_storage_ref).stat().st_mode) == 0o600
    assert protected_payload["url"].endswith("id=top-secret&sort=name")
    assert protected_payload["headers"]["authorization"] == "Bearer source-admin-secret"
    assert protected_payload["headers"]["cookie"] == "session=source-cookie-secret"

    ordinary_record = json.dumps(
        {
            "url": requests[0].normalized_redacted_url,
            "header_names": requests[0].header_names,
            "fingerprint": requests[0].fingerprint,
            "asset_attributes": [asset.attributes for asset in assets],
        },
        sort_keys=True,
    )
    assert "top-secret" not in ordinary_record
    assert "source-admin-secret" not in ordinary_record
    assert "source-cookie-secret" not in ordinary_record


def test_same_asset_is_persisted_independently_for_each_context(db_session, tmp_path) -> None:
    run = make_authenticated_run(db_session)
    storage = SessionStorageService(tmp_path / "protected-captures")
    service = ContextCollectionService(
        gateway=FakeCollectionGateway(),
        storage_service=storage,
    )
    user = next(context for context in run.contexts if context.kind == "user")
    admin = next(context for context in run.contexts if context.kind == "admin")
    user.status = admin.status = "ready"

    service.collect(db_session, run, user)
    service.collect(db_session, run, admin)
    db_session.flush()

    user_identities = set(
        db_session.scalars(select(ScanAsset.identity_key).where(ScanAsset.context_id == user.id))
    )
    admin_identities = set(
        db_session.scalars(select(ScanAsset.identity_key).where(ScanAsset.context_id == admin.id))
    )
    assert user_identities == admin_identities
    assert len(user_identities) == 2
    assert user.asset_count == admin.asset_count == 2
    assert user.request_count == admin.request_count == 1
    assert user.collection_status == admin.collection_status == "completed"
    assert user.status == admin.status == "completed"
    assert all(Path(request.protected_storage_ref).is_file() for request in run.requests)


def test_default_gateway_uses_bounded_http_collection_for_anonymous_context() -> None:
    http_gateway = FakeHttpGateway()
    gateway = DefaultContextCollectionGateway(http_gateway=http_gateway)
    run = SimpleNamespace(
        normalized_url="http://127.0.0.1:8080/start",
        options=ScanOptions(max_pages=2, max_depth=1).model_dump(),
    )
    context = SimpleNamespace(kind="anonymous")

    resources, requests = gateway.collect(run, context)

    assert http_gateway.urls == [
        "http://127.0.0.1:8080/start",
        "http://127.0.0.1:8080/next?id=1",
    ]
    assert [resource.url for resource in resources] == http_gateway.urls
    assert [request.url for request in requests] == http_gateway.urls


def test_default_gateway_preserves_authenticated_request_only_in_observation() -> None:
    inventory_service = FakeInventoryService()
    gateway = DefaultContextCollectionGateway(
        http_gateway=FakeHttpGateway(),
        inventory_service=inventory_service,
    )
    target = SimpleNamespace(base_url="https://app.example/")
    auth_session = SimpleNamespace(id=11)
    run = SimpleNamespace(
        target=target,
        options=ScanOptions(max_pages=3, max_depth=2).model_dump(),
    )
    context = SimpleNamespace(kind="admin", auth_session=auth_session)

    resources, requests = gateway.collect(run, context)

    assert {resource.asset_type for resource in resources} == {"page", "endpoint"}
    assert len(requests) == 1
    assert requests[0].url.endswith("/api/users?id=42")
    assert requests[0].headers == {"authorization": "Bearer exact-source-token"}
    assert requests[0].response_headers == {"content-type": "application/json"}
    controls = inventory_service.calls[0][2]
    assert (controls.max_pages, controls.max_depth) == (3, 2)


def test_pipeline_continues_other_contexts_after_one_collection_failure(db_session) -> None:
    run = make_authenticated_run(db_session)
    for context in run.contexts:
        context.status = "ready"
    db_session.add(
        ScanRunStage(
            scan_run_id=run.id,
            name="collect",
            position=3,
            status="pending",
        )
    )
    db_session.flush()
    collection_service = FailingOneContextCollectionService()
    pipeline = ScanPipeline(
        policy=SimpleNamespace(),
        gateway=SimpleNamespace(),
        context_collection_service=collection_service,
    )

    summaries = pipeline.collect_contexts(db_session, run.id)

    assert collection_service.kinds == ["anonymous", "user", "admin"]
    assert len(summaries) == 2
    failed_user = next(context for context in run.contexts if context.kind == "user")
    assert failed_user.status == "failed"
    assert failed_user.collection_status == "failed"
    assert failed_user.error_code == "context_collection_failed"
    assert "raw-user-collection-secret" not in (failed_user.error_message or "")
    assert run.status == "incomplete"
    assert run.completeness == "missing_user_context"
    stage = next(stage for stage in run.stages if stage.name == "collect")
    assert stage.status == "completed_with_warnings"
