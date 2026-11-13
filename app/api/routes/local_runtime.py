"""Authenticated local-shell controls; never registered in external deployment mode."""

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.errors import ConflictError
from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun
from app.services.collection_executor import executor_operation, quiesce_submissions

router = APIRouter(prefix="/_garden")


@router.get("/activity")
def activity(request: Request):
    service = request.app.state.local_data
    try:
        with service._exclusive():
            pass
    except ConflictError:
        return dict(idle=False, stopping=getattr(request.app.state, "local_stopping", False))
    return dict(idle=True, stopping=getattr(request.app.state, "local_stopping", False))


def quiesce(app, cancel):
    from app.models.delivery_export import DeliveryExport
    from app.models.login_attempt import LoginAttempt

    settings = get_settings()
    with executor_operation(settings.database_url):
        quiesce_submissions(settings.database_url, True)
        app.state.local_stopping = True
        if not cancel:
            try:
                with app.state.local_data._exclusive():
                    pass
            except ConflictError:
                quiesce_submissions(settings.database_url, False)
                app.state.local_stopping = False
                raise
            # Stop the scheduler only after the idle gate has succeeded.
            app.state.project_schedules.stop.set()
            return dict(idle=True)
        app.state.project_schedules.stop.set()
        with session_scope() as session:
            ids = list(
                session.scalars(
                    select(ScanRun.id).where(ScanRun.status.not_in(TERMINAL_SCAN_RUN_STATUSES))
                )
            )
            exports = list(
                session.scalars(
                    select(DeliveryExport.id).where(DeliveryExport.status == "generating")
                )
            )
            logins = list(
                session.scalars(
                    select(LoginAttempt.id).where(
                        LoginAttempt.state.in_(
                            ["created", "waiting_for_user", "validating_restore"]
                        )
                    )
                )
            )
        for run_id in ids:
            app.state.scan_service.cancel_scan(run_id)
        with session_scope() as session:
            for job_id in exports:
                app.state.export_jobs.cancel(session, job_id)
            for attempt_id in logins:
                app.state.manual_login.cancel(session, attempt_id)
    # Cancellation is cooperative and saves existing observations. Wait for worker cleanup
    # outside admission; workers need the same operation lock while finalizing.
    app.state.project_schedules.shutdown()
    app.state.identity_collection.shutdown()
    app.state.scan_service.shutdown()
    app.state.export_jobs.shutdown()
    app.state.manual_login.shutdown()
    return dict(idle=True, cancelled=len(ids))


@router.post("/quiesce")
async def stop_new_requests(request: Request):
    body = await request.json()
    return await run_in_threadpool(quiesce, request.app, body.get("cancel", False))


@router.post("/backup")
async def update_backup(request: Request):
    from app.api.routes.maintenance import BackupRequest
    from app.api.routes.projects import payload

    body = await payload(request, BackupRequest)
    return await run_in_threadpool(request.app.state.local_data.backup, body.destination)
