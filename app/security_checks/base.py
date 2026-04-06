"""Base types for low-risk checks plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.inventory_annotation import InventoryAnnotation
from app.models.inventory_endpoint import InventoryEndpoint
from app.models.inventory_page import InventoryPage
from app.models.inventory_parameter import InventoryParameter
from app.models.inventory_run import InventoryRun
from app.models.scan_job import ScanJob
from app.models.target import Target


@dataclass(frozen=True)
class CheckMetadata:
    name: str
    purpose: str
    category: str
    severity: str
    confidence: str
    trigger_explanation: str
    false_positive_boundaries: str
    remediation_notes: str


@dataclass(frozen=True)
class CheckFindingCandidate:
    title: str
    inventory_refs: list[dict[str, object]]
    reproduction_notes: str
    remediation_notes: str | None = None
    evidence_refs: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class CheckContext:
    inventory_run: InventoryRun
    target: Target
    credential_profile: CredentialProfile
    auth_session: AuthSession
    inventory_job: ScanJob
    pages: list[InventoryPage]
    endpoints: list[InventoryEndpoint]
    parameters: list[InventoryParameter]
    annotations: list[InventoryAnnotation]


class LowRiskCheckPlugin(Protocol):
    metadata: CheckMetadata

    def run(self, context: CheckContext) -> list[CheckFindingCandidate]:
        """Evaluate this check against a structured inventory context."""
