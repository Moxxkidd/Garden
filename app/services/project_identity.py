"""Project-scoped routing and identity keys; never infer missing identity."""

import json
from urllib.parse import urlsplit

from app.schemas.assets import AssetRecord, AssetScope
from app.services.asset_grouping import route_key


def project_asset_key(row: AssetRecord) -> str:
    parsed = urlsplit(row.url)
    if not row.site or not row.method or not parsed.hostname:
        return "isolated:" + row.asset_id
    return "project-route-v1:" + route_key(row)


def project_identity_key(scope: AssetScope, row: AssetRecord) -> str:
    if scope.target_id is not None and row.profile_id is not None:
        return f"target:{scope.target_id}:profile:{row.profile_id}"
    if row.context == "anonymous":
        return f"target:{scope.target_id}:origin:{row.site}:anonymous"
    return json.dumps([scope.source, scope.run_id, row.context_id, row.context])
