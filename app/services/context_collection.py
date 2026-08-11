"""将不同身份上下文的采集结果写入统一验证运行模型。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scan_context import ScanContext
from app.models.scan_request import ScanRequest
from app.models.scan_run import ScanAsset, ScanRun
from app.redaction.service import RedactionService
from app.schemas.inventory import InventoryBuildControls
from app.schemas.scan import ScanOptions
from app.services.coverage_identity import (
    canonical_asset_identity,
    redacted_observed_url,
    stable_response_signature,
)
from app.services.inventory_collection import InventoryCollectionService
from app.services.scan_network import HttpScanGateway
from app.services.session_storage import SessionStorageService


@dataclass(frozen=True)
class ObservedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    status_code: int
    response_headers: dict[str, str]
    response_text: str


@dataclass(frozen=True)
class ObservedResource:
    asset_type: str
    method: str
    url: str
    status_code: int | None
    title: str | None
    attributes: dict[str, object]


@dataclass(frozen=True)
class ContextCollectionSummary:
    context_id: int
    asset_count: int
    request_count: int
    failure_count: int


class ContextCollectionGateway(Protocol):
    def collect(
        self,
        run: ScanRun,
        context: ScanContext,
    ) -> tuple[list[ObservedResource], list[ObservedRequest]]: ...


class DefaultContextCollectionGateway:
    """Adapt passive HTTP and authenticated Playwright collection to one protocol."""

    def __init__(
        self,
        *,
        http_gateway: HttpScanGateway,
        inventory_service: InventoryCollectionService | None = None,
    ) -> None:
        self.http_gateway = http_gateway
        self.inventory_service = inventory_service or InventoryCollectionService()

    def collect(
        self,
        run: ScanRun,
        context: ScanContext,
    ) -> tuple[list[ObservedResource], list[ObservedRequest]]:
        if context.kind == "anonymous":
            return self._collect_anonymous(run)
        return self._collect_authenticated(run, context)

    def _collect_anonymous(
        self,
        run: ScanRun,
    ) -> tuple[list[ObservedResource], list[ObservedRequest]]:
        options = ScanOptions.model_validate(run.options)
        queue: list[tuple[str, int]] = [(run.normalized_url, 0)]
        seen: set[str] = set()
        origin = _origin(run.normalized_url)
        resources: list[ObservedResource] = []
        requests: list[ObservedRequest] = []
        while queue and len(resources) < options.max_pages:
            url, depth = queue.pop(0)
            if url in seen or depth > options.max_depth or _origin(url) != origin:
                continue
            seen.add(url)
            result = self.http_gateway.fetch(url, options)
            resources.append(
                ObservedResource(
                    asset_type="page",
                    method="GET",
                    url=result.final_url,
                    status_code=result.status_code,
                    title=result.title,
                    attributes={
                        "content_type": result.content_type,
                        "body_size_bytes": result.body_size_bytes,
                        "depth": depth,
                        "redirect_count": len(result.redirects),
                        "response_text": result.body_text if result.body_is_text else "",
                    },
                )
            )
            requests.append(
                ObservedRequest(
                    method="GET",
                    url=result.final_url,
                    headers={"accept": "*/*", "user-agent": options.user_agent},
                    body=None,
                    status_code=result.status_code,
                    response_headers=result.headers,
                    response_text=result.body_text if result.body_is_text else "",
                )
            )
            if depth < options.max_depth:
                queue.extend((candidate, depth + 1) for candidate in result.discovered_urls)
        return resources, requests

    def _collect_authenticated(
        self,
        run: ScanRun,
        context: ScanContext,
    ) -> tuple[list[ObservedResource], list[ObservedRequest]]:
        target = run.target
        auth_session = context.auth_session
        if target is None or auth_session is None:
            raise ValueError("认证上下文采集需要 target 和已验证会话。")
        options = ScanOptions.model_validate(run.options)
        result = self.inventory_service.collect(
            target,
            auth_session,
            InventoryBuildControls(
                max_pages=options.max_pages,
                max_depth=options.max_depth,
                max_requests=min(500, max(1, options.max_pages * 10)),
                delay_ms=0,
            ),
        )
        resources = [
            ObservedResource(
                asset_type="page",
                method="GET",
                url=page.url,
                status_code=page.status_code,
                title=page.title,
                attributes={
                    "content_type": "text/html",
                    "cache_control": page.cache_control,
                    "depth": page.depth,
                    "response_text": page.text_preview or "",
                },
            )
            for page in result.pages
        ]
        requests: list[ObservedRequest] = []
        for endpoint in result.endpoints:
            exact_url = endpoint.request_url or endpoint.url
            resources.append(
                ObservedResource(
                    asset_type="endpoint",
                    method=endpoint.method,
                    url=exact_url,
                    status_code=endpoint.status_code,
                    title=None,
                    attributes={
                        "content_type": endpoint.content_type,
                        "cache_control": endpoint.cache_control,
                        "parameter_names": endpoint.parameters,
                        "response_text": endpoint.response_preview or "",
                    },
                )
            )
            response_headers = dict(endpoint.response_headers)
            if endpoint.content_type and "content-type" not in response_headers:
                response_headers["content-type"] = endpoint.content_type
            if endpoint.cache_control and "cache-control" not in response_headers:
                response_headers["cache-control"] = endpoint.cache_control
            requests.append(
                ObservedRequest(
                    method=endpoint.method,
                    url=exact_url,
                    headers=dict(endpoint.request_headers),
                    body=endpoint.request_body,
                    status_code=endpoint.status_code,
                    response_headers=response_headers,
                    response_text=endpoint.response_preview or "",
                )
            )
        return resources, requests


class ContextCollectionService:
    """Persist gateway-neutral observations without ordinary-field secrets."""

    def __init__(
        self,
        *,
        gateway: ContextCollectionGateway,
        storage_service: SessionStorageService | None = None,
        redaction_service: RedactionService | None = None,
    ) -> None:
        self.gateway = gateway
        self.storage_service = storage_service or SessionStorageService()
        self.redaction_service = redaction_service or RedactionService()

    def collect(
        self,
        session: Session,
        run: ScanRun,
        context: ScanContext,
    ) -> ContextCollectionSummary:
        if context.scan_run_id != run.id:
            raise ValueError("采集上下文不属于指定验证运行。")
        resources, requests = self.gateway.collect(run, context)
        asset_by_identity: dict[str, ScanAsset] = {}
        for resource in resources:
            asset = self._persist_resource(session, run, context, resource)
            asset_by_identity[asset.identity_key or ""] = asset
        session.flush()

        seen_request_fingerprints: set[str] = set()
        for observed in requests:
            identity_key = canonical_asset_identity(observed.method, observed.url)
            asset = asset_by_identity.get(identity_key)
            if asset is None:
                asset = self._persist_resource(
                    session,
                    run,
                    context,
                    ObservedResource(
                        asset_type="endpoint",
                        method=observed.method,
                        url=observed.url,
                        status_code=observed.status_code,
                        title=None,
                        attributes={
                            "content_type": observed.response_headers.get("content-type"),
                            "response_text": observed.response_text,
                        },
                    ),
                )
                asset_by_identity[identity_key] = asset
                session.flush()
            fingerprint = self._request_fingerprint(observed)
            if fingerprint in seen_request_fingerprints:
                continue
            seen_request_fingerprints.add(fingerprint)
            storage_ref = self.storage_service.write_request_payload(
                run.id,
                context.id,
                {
                    "method": observed.method.upper(),
                    "url": observed.url,
                    "headers": observed.headers,
                    "body": observed.body.decode("utf-8", errors="replace")
                    if observed.body is not None
                    else None,
                    "status_code": observed.status_code,
                    "response_headers": observed.response_headers,
                    "response_text": observed.response_text,
                },
            )
            session.add(
                ScanRequest.from_capture(
                    scan_run_id=run.id,
                    source_context_id=context.id,
                    asset_id=asset.id,
                    method=observed.method,
                    raw_url=observed.url,
                    header_names=list(observed.headers),
                    fingerprint=fingerprint,
                    protected_storage_ref=storage_ref,
                )
            )

        session.flush()
        assets = list(
            session.scalars(
                select(ScanAsset).where(
                    ScanAsset.scan_run_id == run.id,
                    ScanAsset.context_id == context.id,
                )
            )
        )
        stored_requests = list(
            session.scalars(
                select(ScanRequest).where(
                    ScanRequest.scan_run_id == run.id,
                    ScanRequest.source_context_id == context.id,
                )
            )
        )
        context.status = "completed"
        context.collection_status = "completed"
        context.completeness = "complete"
        context.asset_count = len(assets)
        context.request_count = len(stored_requests)
        context.finished_at = datetime.now(timezone.utc)
        session.flush()
        return ContextCollectionSummary(
            context_id=context.id,
            asset_count=context.asset_count,
            request_count=context.request_count,
            failure_count=context.failure_count,
        )

    def _persist_resource(
        self,
        session: Session,
        run: ScanRun,
        context: ScanContext,
        resource: ObservedResource,
    ) -> ScanAsset:
        identity_key = canonical_asset_identity(resource.method, resource.url)
        asset = session.scalar(
            select(ScanAsset).where(
                ScanAsset.scan_run_id == run.id,
                ScanAsset.context_id == context.id,
                ScanAsset.identity_key == identity_key,
            )
        )
        attributes = dict(resource.attributes)
        response_text = attributes.pop("response_text", None)
        content_type = attributes.get("content_type")
        if isinstance(response_text, str):
            attributes["content_signature"] = stable_response_signature(
                response_text,
                content_type if isinstance(content_type, str) else None,
            )
        redacted_attributes = self.redaction_service.redact_mapping(attributes)
        if asset is None:
            asset = ScanAsset(
                scan_run_id=run.id,
                context_id=context.id,
                identity_key=identity_key,
                asset_type=resource.asset_type,
                url=redacted_observed_url(resource.url),
                method=resource.method.upper(),
                status_code=resource.status_code,
                title=self.redaction_service.redact_text(resource.title, limit=500)
                if resource.title
                else None,
                attributes=redacted_attributes,
                discovered_at=datetime.now(timezone.utc),
            )
            session.add(asset)
            session.flush()
            return asset
        asset.status_code = resource.status_code
        asset.title = (
            self.redaction_service.redact_text(resource.title, limit=500)
            if resource.title
            else asset.title
        )
        asset.attributes.update(redacted_attributes)
        return asset

    def _request_fingerprint(self, observed: ObservedRequest) -> str:
        content_type = observed.response_headers.get("content-type")
        payload = {
            "method": observed.method.upper(),
            "identity": canonical_asset_identity(observed.method, observed.url),
            "body_hash": hashlib.sha256(observed.body or b"").hexdigest(),
            "response_status": observed.status_code,
            "response_signature": stable_response_signature(
                observed.response_text,
                content_type,
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
    )
