"""Inventory collection orchestration helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.integrations.inventory.playwright_gateway import (
    InventoryCollectionResult,
    PlaywrightInventoryGatewayProtocol,
    SyncPlaywrightInventoryGateway,
)
from app.models.auth_session import AuthSession
from app.models.enums import SessionType
from app.models.target import Target
from app.schemas.inventory import InventoryBuildControls
from app.services.session_storage import SessionStorageService


@dataclass
class CollectionContext:
    start_url: str
    session_payload: dict[str, Any]
    session_type: SessionType


class InventoryCollectionService:
    """Resolve a reusable session into Playwright inventory collection inputs."""

    def __init__(
        self,
        *,
        storage_service: SessionStorageService | None = None,
        gateway: PlaywrightInventoryGatewayProtocol | None = None,
    ) -> None:
        self.storage_service = storage_service or SessionStorageService()
        self.gateway = gateway or SyncPlaywrightInventoryGateway()

    def collect(
        self,
        target: Target,
        auth_session: AuthSession,
        controls: InventoryBuildControls,
        *,
        start_url: str | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> InventoryCollectionResult:
        context = self.build_collection_context(target, auth_session)
        kwargs: dict[str, object] = {}
        if before_request is not None:
            kwargs["before_request"] = before_request
        return self.gateway.collect(
            target=target,
            start_url=start_url or context.start_url,
            controls=controls,
            session_type=context.session_type,
            session_payload=context.session_payload,
            **kwargs,
        )

    def build_collection_context(
        self,
        target: Target,
        auth_session: AuthSession,
    ) -> CollectionContext:
        payload = self.storage_service.read_payload(auth_session.storage_ref)
        session_type = SessionType(auth_session.session_type)
        metadata = auth_session.session_metadata_redacted or {}
        start_url = self._resolve_start_url(target, metadata)
        return CollectionContext(
            start_url=start_url,
            session_payload=payload,
            session_type=session_type,
        )

    def _resolve_start_url(self, target: Target, metadata: dict[str, object]) -> str:
        current_url = metadata.get("current_url")
        if isinstance(current_url, str) and self._same_origin(target.base_url, current_url):
            return current_url
        validate_url = metadata.get("validate_url")
        if isinstance(validate_url, str) and self._same_origin(target.base_url, validate_url):
            return validate_url
        login_url = metadata.get("login_url")
        if isinstance(login_url, str) and self._same_origin(target.base_url, login_url):
            return login_url
        return target.base_url

    def _same_origin(self, left: str, right: str) -> bool:
        return self._origin(left) == self._origin(right)

    def _origin(self, value: str) -> str:
        parsed = urlparse(value)
        return f"{parsed.scheme}://{parsed.netloc}"
