"""正式 CLI 与本机 Garden Web UI 之间的最小 HTTP 适配。"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.errors import GardenError
from app.schemas.scan import ScanOptions, ScanRunView


class LocalApiError(GardenError):
    """本机 Web UI 未就绪或返回不可用响应。"""


class LocalScanApi:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def start_scan(self, url: str, options: ScanOptions) -> ScanRunView:
        return self._scan_request(
            "POST", "/api/scans", {"url": url, "options": options.model_dump()}
        )

    def get_scan(self, scan_run_id: int) -> ScanRunView:
        return self._scan_request("GET", f"/api/scans/{scan_run_id}")

    def cancel_scan(self, scan_run_id: int) -> ScanRunView:
        return self._scan_request("POST", f"/api/scans/{scan_run_id}/cancel")

    def _scan_request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> ScanRunView:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"content-type": "application/json"} if data else {},
        )
        try:
            with urlopen(request, timeout=5) as response:  # noqa: S310 - loopback URL only
                return ScanRunView.model_validate(json.loads(response.read()))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise LocalApiError(f"本机 Garden Web UI 返回 HTTP {error.code}: {detail}") from error
        except (URLError, OSError, ValueError) as error:
            raise LocalApiError(f"无法连接本机 Garden Web UI：{error}") from error
