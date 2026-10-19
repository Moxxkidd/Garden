"""Public delivery-v1 contract, independent of internal persistence models."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.assets import AssetRecord, AssetVariant


class DeliveredAsset(BaseModel):
    record_type: Literal["asset"] = "asset"
    id: str
    url: str
    site: str | None
    method: str | None
    kind: str
    first_seen: str | None
    last_seen: str | None
    observation_count: int
    tags: list[str]
    note: str


class DeliveredObservation(BaseModel):
    record_type: Literal["observation"] = "observation"
    id: int
    asset_id: str
    display_asset_id: str
    project_run_id: int
    source_key: str
    identity_key: str
    seen_at: str | None
    first_seen: str | None
    record: AssetRecord


class DeliveredVariant(BaseModel):
    record_type: Literal["variant"] = "variant"
    id: str
    observation_id: int
    asset_id: str
    display_asset_id: str
    project_run_id: int
    identity_key: str
    variant: AssetVariant | None
    identity_status: Literal["known", "unknown"]
    note: str


class DeliveredCandidate(BaseModel):
    record_type: Literal["candidate"] = "candidate"
    id: int
    project_run_id: int
    asset_id: str | None
    source_key: str
    record: AssetRecord


class DeliveredRelation(BaseModel):
    record_type: Literal["relation"] = "relation"
    id: int
    project_run_id: int
    kind: str
    from_asset_id: str | None
    to_asset_id: str | None
    detail: dict


class DeliveredChange(BaseModel):
    record_type: Literal["change"] = "change"
    id: str
    asset_id: str
    identity_key: str
    status: Literal["newly_observed", "attributes_changed", "unchanged", "not_observed", "unknown"]
    observed_attributes_differ: bool
    reasons: list[str]
    before_observation_ids: list[int]
    after_observation_ids: list[int]


DeliveryRow = Annotated[
    DeliveredAsset
    | DeliveredObservation
    | DeliveredVariant
    | DeliveredCandidate
    | DeliveredRelation
    | DeliveredChange,
    Field(discriminator="record_type"),
]


class DeliveryDocument(BaseModel):
    schema_version: Literal["delivery-v1"] = "delivery-v1"
    project_id: int
    revision: int
    dataset: Literal["assets", "observations", "variants", "candidates", "relations", "changes"]
    filters: dict
    matched: int
    exported_count: int
    next_cursor: str | None = None
    items: list[DeliveryRow]
    source_coverage: list[dict]
    coverage_summary: dict[str, int]
    limitations: list[str]
