"""Finding-related schemas."""

from pydantic import BaseModel


class CheckRunSummary(BaseModel):
    inventory_run_id: int
    scan_job_id: int
    target_name: str
    profile_name: str
    session_id: int
    findings_created: int
    findings_updated: int
    total_findings: int
    summary: str
