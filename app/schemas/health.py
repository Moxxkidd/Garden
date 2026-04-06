"""Health response schema."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    project: str
    version: str
    database: str
    environment: str
    allow_non_local_targets: bool
