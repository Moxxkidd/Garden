import json

import httpx
from typer.testing import CliRunner

from app.cli.main import app
from app.db.bootstrap import session_scope
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from app.services.collection_queue import CollectionQueueService
from tests.test_url_scan_pipeline import _service


def test_queue_cli_shows_preview_and_cancels_without_starting(seeded_records, tmp_path):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/next">next</a>'
        )

    from app.models.target import Target
    from tests.helpers.project_assets import project_fixture

    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        pid, eid = project.id, entry.id
    service = _service(tmp_path, handle)
    try:
        run = service.start_assessment(
            AssessmentStartRequest(
                url="http://localhost:8111/",
                target_id=seeded_records["target"].id,
                project_entry_id=eid,
                options=ScanOptions(durable=CollectionOptions(max_requests=1, min_interval_ms=0)),
            )
        )
        with session_scope() as session:
            qid = CollectionQueueService().for_run(session, run.id)[0]["id"]
        runner = CliRunner()
        shown = runner.invoke(app, ["queue", "show", str(run.id)])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.stdout)[0]["metrics"]["requests_used"] == 1
        work = runner.invoke(app, ["queue", "work", str(qid)])
        assert work.exit_code == 0, work.output
        assert json.loads(work.stdout)["total"] == 2
        exported = runner.invoke(app, ["queue", "export", str(qid), "--format", "json"])
        assert exported.exit_code == 0, exported.output
        assert json.loads(exported.stdout)["total"] == 2
        cancelled = runner.invoke(app, ["queue", "resume", str(qid)], input="n\n")
        assert cancelled.exit_code == 0, cancelled.output
        assert "max_requests" in cancelled.output
        assert "取消" in cancelled.output
        assert len(sent) == 1
        incremental = runner.invoke(
            app,
            [
                "projects",
                "incremental",
                str(pid),
                "--entry-id",
                str(eid),
                "--preview-only",
                "--max-evidence-bytes",
                "4096",
                "--max-variants",
                "3",
                "--origin-concurrency",
                "2",
                "--retry-attempts",
                "0",
            ],
        )
        assert incremental.exit_code == 0, incremental.output
        assert json.loads(incremental.stdout)["work_count"] == 1
        expected = {
            "max_evidence_bytes": 4096,
            "max_variants": 3,
            "origin_concurrency": 2,
            "retry_attempts": 0,
        }
        assert expected.items() <= json.loads(incremental.stdout)["options"].items()
        recovery = runner.invoke(
            app,
            [
                "queue",
                "resume",
                str(qid),
                "--preview-only",
                "--max-evidence-bytes",
                "4096",
                "--max-variants",
                "3",
                "--origin-concurrency",
                "2",
                "--retry-attempts",
                "0",
            ],
        )
        assert recovery.exit_code == 0, recovery.output
        assert expected.items() <= json.loads(recovery.stdout)["options"].items()
        assert len(sent) == 1
        assets = runner.invoke(app, ["projects", "assets", str(pid), "--json"])
        assert assets.exit_code == 0, assets.output
        asset_id = json.loads(assets.stdout)["items"][0]["id"]
        important = runner.invoke(
            app,
            [
                "projects",
                "incremental",
                str(pid),
                "--entry-id",
                str(eid),
                "--important-asset-id",
                asset_id,
                "--preview-only",
            ],
        )
        assert important.exit_code == 0, important.output
        chosen = json.loads(important.stdout)["work"][0]
        assert chosen["reason"] == "important" and asset_id in chosen["asset_ids"]
        assert len(sent) == 1
        paused = runner.invoke(app, ["queue", "pause", str(qid)])
        assert paused.exit_code == 0, paused.output
        assert json.loads(paused.stdout)["status"] == "paused"
    finally:
        service.shutdown()
