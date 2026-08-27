"""Passive authenticated coverage HTTP adapters."""

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse, PlainTextResponse

from app.schemas.assessment import (
    AssessmentRunView,
    CoverageDifferenceView,
    PassiveCoverageStartRequest,
)

router = APIRouter(prefix="/api/assessments", tags=["assessments"])


@router.post("", response_model=AssessmentRunView, status_code=status.HTTP_202_ACCEPTED)
def start_assessment(
    request: Request,
    payload: PassiveCoverageStartRequest,
) -> AssessmentRunView:
    return request.app.state.scan_service.start_assessment(payload.to_assessment_request())


@router.get("/{assessment_id}", response_model=AssessmentRunView)
def get_assessment(request: Request, assessment_id: int) -> AssessmentRunView:
    return request.app.state.scan_service.get_assessment(assessment_id)


@router.get(
    "/{assessment_id}/differences",
    response_model=list[CoverageDifferenceView],
)
def list_differences(
    request: Request,
    assessment_id: int,
) -> list[CoverageDifferenceView]:
    return request.app.state.scan_service.list_coverage_differences(assessment_id)


@router.post("/{assessment_id}/cancel", response_model=AssessmentRunView)
def cancel_assessment(request: Request, assessment_id: int) -> AssessmentRunView:
    return request.app.state.scan_service.cancel_assessment(assessment_id)


@router.get("/{assessment_id}/report")
def assessment_report(
    request: Request,
    assessment_id: int,
    download: bool = Query(default=False),
):
    report = request.app.state.scan_service.read_report(assessment_id)
    if download:
        view = request.app.state.scan_service.get_assessment(assessment_id)
        return FileResponse(
            view.report_path,
            media_type="text/markdown; charset=utf-8",
            filename=f"garden-assessment-{assessment_id}.md",
        )
    return PlainTextResponse(report, media_type="text/markdown; charset=utf-8")
