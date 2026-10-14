import html
import json
import re
from html.parser import HTMLParser

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from tests.test_url_scan_pipeline import _service


def test_queue_native_form_previews_before_creating_linked_run(seeded_records, tmp_path):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/next">next</a><a href="/other">other</a>',
        )

    scans = _service(tmp_path, handle)
    app = create_app()
    app.state.scan_service = scans
    client = TestClient(app)
    try:
        run = scans.start_assessment(
            AssessmentStartRequest(
                url="http://localhost:8111/",
                target_id=seeded_records["target"].id,
                options=ScanOptions(durable=CollectionOptions(max_requests=1, min_interval_ms=0)),
            )
        )
        page = client.get(f"/scans/{run.id}/queue")
        assert page.status_code == 200
        assert "已知队列仍未覆盖完整" in page.text
        assert page.headers["cache-control"] == "no-store"

        class FormParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.fields = {}
                self.active = False

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "form":
                    self.active = attrs.get("action", "").endswith("/preview")
                if self.active and tag == "input" and attrs.get("name"):
                    self.fields[attrs["name"]] = attrs.get("value", "")

            def handle_endtag(self, tag):
                if tag == "form":
                    self.active = False

        parsed = FormParser()
        parsed.feed(page.text)
        assert "max_requests" in parsed.fields
        assert "work_ids" in parsed.fields
        queue = client.get(f"/api/scans/{run.id}/queue").json()[0]
        items = client.get(f"/api/scans/{run.id}/queue/{queue['id']}/work").json()["items"]
        selected = next(item["id"] for item in items if item["url"].endswith("/other"))
        parsed.fields["work_ids"] = str(selected)
        parsed.fields.update(max_requests="10", min_interval_ms="0", max_evidence_bytes="4096")
        preview = client.post(
            f"/scans/{run.id}/queue/preview",
            data=parsed.fields,
        )
        assert preview.status_code == 200
        assert "确认创建新一轮" in preview.text
        assert len(sent) == 1
        fields = dict(
            (name, html.unescape(value))
            for name, value in re.findall(
                r'<input type="hidden" name="([^"]+)" value="([^"]*)"', preview.text
            )
        )
        assert json.loads(fields["request"])["options"]["max_evidence_bytes"] == 4096
        done = client.post(f"/scans/{run.id}/queue/resume", data=fields, follow_redirects=False)
        assert done.status_code == 303
        assert sent == ["/", "/other"]
        assert done.headers["location"].startswith("/scans/")
        assert f"/scans/{run.id}/queue" in client.get(f"/scans/{run.id}").text
    finally:
        client.close()
        scans.shutdown()
