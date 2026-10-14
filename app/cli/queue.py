"""Explicit local queue inspection and new-round confirmation."""

from typing import Annotated

import typer

from app.cli.projects import emit, guarded
from app.db.bootstrap import session_scope
from app.schemas.collection_queue import CollectionOptions
from app.schemas.collection_resume import CollectionResumeRequest
from app.services.collection_queue import CollectionQueueService
from app.services.collection_resume import CollectionResumeService

app = typer.Typer(help="持久采集队列：查询、暂停和明确确认的新轮补采。", no_args_is_help=True)


@app.command("show")
@guarded
def show(run_id: int):
    from app.api.routes.collection_queue import check_run

    with session_scope() as session:
        check_run(session, run_id)
        emit(CollectionQueueService().for_run(session, run_id))


@app.command("work")
@guarded
def work(queue_id: int, state: str | None = None, page: int = 1, page_size: int = 50):
    with session_scope() as session:
        emit(
            CollectionQueueService().list_work(
                session, queue_id, state=state, page=page, page_size=page_size
            )
        )


@app.command("pause")
@guarded
def pause(queue_id: int):
    with session_scope() as session:
        emit(CollectionQueueService().pause(session, queue_id))


@app.command("resume")
@guarded
def resume(
    queue_id: int,
    work_id: Annotated[list[int] | None, typer.Option("--work-id")] = None,
    mode: str = "known_only",
    max_requests: int = 1000,
    max_work: int = 2000,
    max_evidence_bytes: int = 32 * 1024 * 1024,
    max_variants: int = 20,
    origin_concurrency: int = 1,
    retry_attempts: int = 1,
    min_interval_ms: int = 250,
    preview_only: bool = typer.Option(False, "--preview-only"),
    yes: bool = typer.Option(False, "--yes"),
):
    request = CollectionResumeRequest(
        queue_id=queue_id,
        work_ids=work_id,
        mode=mode,
        options=CollectionOptions(
            max_requests=max_requests,
            max_work=max_work,
            min_interval_ms=min_interval_ms,
            max_evidence_bytes=max_evidence_bytes,
            max_variants=max_variants,
            origin_concurrency=origin_concurrency,
            retry_attempts=retry_attempts,
        ),
    )
    planner = CollectionResumeService()
    with session_scope() as session:
        preview = planner.preview(session, request)
    emit(preview)
    if preview_only:
        return
    if not yes and not typer.confirm("使用上述预算创建新一轮补采？", default=False):
        typer.echo("已取消；未创建任务。")
        return
    from app.services.identity_collection import IdentityCollectionService
    from app.services.scan_application import InlineScanDispatcher, ScanApplicationService

    scans = ScanApplicationService(dispatcher=InlineScanDispatcher())
    identities = IdentityCollectionService(dispatcher=InlineScanDispatcher())
    try:
        planner.scans, planner.identities = scans, identities
        with session_scope() as session:
            emit({"run_id": planner.start(session, request, preview["token"])})
    finally:
        identities.shutdown()
        scans.shutdown()


@app.command("export")
@guarded
def export(queue_id: int, format: str = "json", state: str | None = None):
    from app.services.collection_export import export_collection

    with session_scope() as session:
        typer.echo(export_collection(session, queue_id, format, state=state))
