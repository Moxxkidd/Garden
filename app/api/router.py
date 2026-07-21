"""API router composition."""

from fastapi import APIRouter

from app.api.routes.credentials import router as credentials_router
from app.api.routes.demo_auth import router as demo_auth_router
from app.api.routes.evidence import router as evidence_router
from app.api.routes.findings import router as findings_router
from app.api.routes.health import router as health_router
from app.api.routes.inventory import router as inventory_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.pages import router as pages_router
from app.api.routes.scans import router as scans_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.targets import router as targets_router

api_router = APIRouter()
api_router.include_router(pages_router)
api_router.include_router(scans_router)
api_router.include_router(targets_router)
api_router.include_router(credentials_router)
api_router.include_router(jobs_router)
api_router.include_router(sessions_router)
api_router.include_router(inventory_router)
api_router.include_router(findings_router)
api_router.include_router(evidence_router)
api_router.include_router(demo_auth_router)
api_router.include_router(health_router)
