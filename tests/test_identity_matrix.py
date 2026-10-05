"""One identity projection across catalog, CLI, Web, export and report."""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli
from app.db.bootstrap import session_scope
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanRun
from app.services.scan_reporting import ScanReportService


def test_catalog_cli_web_export_report_share_identity_matrix(app, seeded_records, tmp_path):
    with session_scope() as session:
        run = ScanRun(
            mode="identity_collection",
            target_id=seeded_records["target"].id,
            input_url="https://example.test",
            normalized_url="https://example.test",
            status="completed_with_warnings",
            completeness="incomplete",
        )
        session.add(run)
        session.flush()
        for i, code in [(1, 200), (2, 403), (3, None)]:
            context = ScanContext(
                scan_run_id=run.id,
                kind="identity",
                context_key=f"profile:{i}",
                identity_snapshot={"name": "Reader"},
                health_status="ready" if code else "unknown",
                completeness="complete" if code else "incomplete",
            )
            session.add(context)
            session.flush()
            if code:
                session.add(
                    ScanAsset(
                        scan_run_id=run.id,
                        context_id=context.id,
                        asset_type="page",
                        method="GET",
                        url="https://example.test/asset?token=PRIVATE",
                        status_code=code,
                        attributes={"identity_assessment": "confirmed"},
                        discovered_at=datetime.now(timezone.utc),
                    )
                )
        session.flush()
        run_id = run.id
        report = ScanReportService(tmp_path).generate(session, run.id)
    query = f"source=scan&run_id={run_id}&view=grouped"
    with TestClient(app) as client:
        response = client.get("/api/assets?" + query)
        assert response.status_code == 200, response.text
        payload = response.json()
        matrix = payload["identity_matrix"]
        assert matrix["confirmed_subject_count"] == 1
        assert len(payload["items"]) == 1
        assert len(payload["items"][0]["observations"]) == 2
        assert payload["items"][0]["contexts"] == ["profile:1", "profile:2"]
        assert matrix["rows"][0]["cells"]["profile:3"]["state"] == "unknown"
        assert client.get("/api/assets/export?" + query).json()["identity_matrix"] == matrix
        html = client.get("/assets?" + query).text
        assert "身份可见性矩阵" in html
        assert "profile:1" in html and "profile:2" in html
        assert "PRIVATE" not in html
        assert "profile:1" in client.get("/api/assets/export?" + query + "&format=csv").text
    result = CliRunner().invoke(
        cli,
        [
            "assets",
            "list",
            "--source",
            "scan",
            "--run-id",
            str(run_id),
            "--view",
            "grouped",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["identity_matrix"] == matrix
    text = Path(report).read_text()
    assert "身份可见性矩阵" in text and "1 类资产主体" in text
    assert "PRIVATE" not in text
