"""Local export jobs reuse the bounded executor and release DB reads before delivery."""

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.cli.paths import formal_runtime_paths
from app.core.errors import ConflictError, GardenError, ResourceNotFoundError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.core.version import project_version
from app.db.bootstrap import session_scope
from app.models.delivery_export import DeliveryExport
from app.schemas.delivery import DeliveryExportRequest
from app.services.asset_delivery import AssetDeliveryService
from app.services.delivery_files import PdfUnavailableError, render_snapshot
from app.services.scan_application import ThreadedScanDispatcher

logger = get_logger(__name__)
MEDIA = {
    "json": "application/json",
    "csv": "text/csv",
    "readable_csv": "text/csv",
    "markdown": "text/markdown",
    "html": "text/html",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "openapi": "application/json",
}


class ExportCancelled(RuntimeError):
    pass


class ExportJobService:
    def __init__(self, *, base_directory=None, dispatcher=None):
        settings = get_settings()
        paths = formal_runtime_paths()
        self.directory = (
            Path(base_directory)
            if base_directory
            else (paths.storage_dir / "exports" if paths else Path.cwd() / "data" / "exports")
        )
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dispatcher = dispatcher or ThreadedScanDispatcher(
            settings.scan_max_concurrent_tasks, database_url=settings.database_url
        )
        self.controls = {}
        self.lock = threading.RLock()
        self.delivery = AssetDeliveryService()

    def submit(self, session, project_id, request: DeliveryExportRequest, *, inline=False):
        request = request.model_copy(update={"query": request.query.model_copy(update={"page": 1})})
        metadata = self.delivery.metadata(
            session,
            project_id,
            request.query,
            dataset=request.dataset,
            before_run_id=request.before_run_id,
            after_run_id=request.after_run_id,
        )
        if (
            request.expected_revision is not None
            and request.expected_revision != metadata["revision"]
        ):
            raise ConflictError("delivery_revision_changed：项目已变化，请重新预览。")
        if request.format == "readable_csv" and metadata["dataset"] not in {
            "assets",
            "observations",
            "candidates",
        }:
            from app.core.errors import InputValidationError

            raise InputValidationError("可读 CSV 仅用于资产、身份观察或候选。")
        if request.format == "openapi" and metadata["dataset"] != "observations":
            from app.core.errors import InputValidationError

            raise InputValidationError("OpenAPI 草稿需要选择身份观察数据集。")
        job = DeliveryExport(
            id=uuid4().hex,
            project_id=project_id,
            revision=metadata["revision"],
            configuration=request.model_dump(mode="json"),
            status="generating",
        )
        session.add(job)
        session.commit()
        job_id = job.id
        with self.lock:
            self.controls[job_id] = threading.Event()
        try:
            if inline:
                self.generate(job_id)
            else:
                self.dispatcher.submit(job_id, self.generate)
        except Exception:
            self.fail(job_id, "export_dispatch_failed")
            with self.lock:
                self.controls.pop(job_id, None)
            raise
        return self.view(session, job_id)

    def _job(self, session, job_id):
        job = session.get(DeliveryExport, job_id)
        if not job:
            raise ResourceNotFoundError("export_not_found")
        session.refresh(job)
        return job

    def view(self, session, job_id):
        job = self._job(session, job_id)
        return dict(
            id=job.id,
            project_id=job.project_id,
            revision=job.revision,
            format=job.configuration["format"],
            dataset=job.configuration["dataset"] or job.configuration["query"]["view"],
            status=job.status,
            error_code=job.error_code,
            download_url=f"/api/exports/{job.id}/download" if job.status == "ready" else None,
        )

    def cancel(self, session, job_id):
        with self.lock:
            job = self._job(session, job_id)
            if job.status != "generating":
                raise ConflictError("export_not_generating")
            self.controls[job_id].set()
            job.status, job.artifact = "cancelled", None
            session.commit()
        return self.view(session, job_id)

    def artifact(self, session, job_id):
        job = self._job(session, job_id)
        if job.status != "ready":
            raise ConflictError("export_not_ready")
        return self.directory / job.id / job.artifact, MEDIA[job.configuration["format"]]

    def fail(self, job_id, reason, *, cancelled=False):
        with session_scope() as session:
            job = self._job(session, job_id)
            if job.status == "generating":
                job.status = "cancelled" if cancelled else "failed"
                job.error_code, job.artifact = reason, None
                session.commit()

    def generate(self, job_id):
        directory = self.directory / job_id
        ready = False
        with self.lock:
            control = self.controls.get(job_id)
        if control is None:
            return

        def check():
            if control.is_set():
                raise ExportCancelled

        try:
            with session_scope() as session:
                job = self._job(session, job_id)
                if job.status != "generating":
                    return
                request = DeliveryExportRequest.model_validate(job.configuration)
                args = dict(
                    dataset=request.dataset,
                    before_run_id=request.before_run_id,
                    after_run_id=request.after_run_id,
                )
                metadata = self.delivery.metadata(session, job.project_id, request.query, **args)
                if metadata["revision"] != job.revision:
                    raise ConflictError("delivery_revision_changed")
                metadata.update(
                    garden_version=project_version(),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
                directory.mkdir(mode=0o700)
                (directory / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
                )

                def capture(name, rows):
                    with (directory / name).open("w", encoding="utf-8") as output:
                        for row in rows:
                            check()
                            output.write(json.dumps(row, ensure_ascii=False) + "\n")

                capture(
                    "items.jsonl",
                    self.delivery.iter_rows(session, job.project_id, request.query, **args),
                )
                if metadata["dataset"] == "assets":
                    capture(
                        "observations.jsonl",
                        self.delivery.iter_rows(
                            session, job.project_id, request.query, dataset="observations"
                        ),
                    )
            # This scope has no Session. Rendering and downloads read only disk files.
            check()
            partial = render_snapshot(directory, request.format, check)
            with self.lock, session_scope() as session:
                check()
                job = self._job(session, job_id)
                if job.status != "generating":
                    return
                destination = partial.with_suffix("")
                partial.replace(destination)
                job.artifact, job.status = destination.name, "ready"
                session.commit()
                ready = True
        except ExportCancelled:
            self.fail(job_id, "export_cancelled", cancelled=True)
        except PdfUnavailableError:
            self.fail(job_id, "pdf_unavailable")
        except GardenError as error:
            self.fail(
                job_id,
                "delivery_revision_changed"
                if isinstance(error, ConflictError)
                else "export_invalid_configuration",
            )
        except Exception:
            self.fail(job_id, "export_generation_failed")
            logger.exception("Export generation failed", extra={"export_id": job_id})
            raise
        finally:
            with self.lock:
                self.controls.pop(job_id, None)
            if not ready and directory.exists():
                shutil.rmtree(directory)

    def interrupt_active(self):
        with session_scope() as session:
            rows = list(
                session.scalars(select(DeliveryExport).where(DeliveryExport.status == "generating"))
            )
            ids = [r.id for r in rows]
            for job in rows:
                job.status, job.error_code, job.artifact = "failed", "export_interrupted", None
            session.commit()
        for job_id in ids:
            path = self.directory / job_id
            if path.exists():
                shutil.rmtree(path)
        return len(ids)

    def shutdown(self):
        self.dispatcher.shutdown()
