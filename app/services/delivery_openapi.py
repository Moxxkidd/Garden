"""Observed endpoint hints, never a claim of a complete or executable API contract."""

from urllib.parse import parse_qsl, urlsplit


def openapi_draft(document):
    paths = {}
    excluded = 0
    for observation in document["items"]:
        row = observation["record"]
        method = (row["method"] or "").lower()
        url = urlsplit(row["url"])
        if (
            row["kind"] != "endpoint"
            or row["observation"] != "response_observed"
            or row["route_url"]
            or url.fragment
            or url.scheme not in {"http", "https"}
            or method not in {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
            or "{" in url.path
            or "}" in url.path
        ):
            excluded += 1
            continue
        operation = paths.setdefault(url.path or "/", {}).setdefault(
            method,
            {
                "summary": "Observed endpoint — inferred draft",
                "x-garden-inferred": True,
                "x-garden-missing-fields": [
                    "requestBody",
                    "authentication",
                    "responseSchema",
                    "parameterTypes",
                    "parameterRequiredness",
                ],
                "servers": [],
                "parameters": [],
                "responses": {},
                "x-garden-observations": [],
            },
        )
        server = {"url": f"{url.scheme}://{url.netloc}"}
        if server not in operation["servers"]:
            operation["servers"].append(server)
            operation["servers"].sort(key=lambda s: s["url"])
        for name in sorted({k for k, _ in parse_qsl(url.query, keep_blank_values=True)}):
            parameter = {
                "name": name,
                "in": "query",
                "schema": {},
                "description": "Observed name only; value, type and requiredness unknown.",
                "x-garden-inferred": True,
            }
            if parameter not in operation["parameters"]:
                operation["parameters"].append(parameter)
        operation["parameters"].sort(key=lambda p: p["name"])
        for code in row["status_codes"]:
            operation["responses"][str(code)] = {
                "description": "Observed response; schema unknown."
            }
        operation["x-garden-observations"].append(
            {
                "observation_id": observation["id"],
                "identity_key": observation["identity_key"],
                "identity_assessment": row["identity_assessment"],
                "origin": server["url"],
                "status_codes": row["status_codes"],
                "content_type": row["content_type"],
                "evidence_url": row["source_url"],
            }
        )
    for path in paths.values():
        for operation in path.values():
            if not operation["responses"]:
                operation["responses"] = {"default": {"description": "Status code not recorded."}}
    return {
        "openapi": "3.1.1",
        "info": {
            "title": f"Garden project {document['project_id']} — inferred draft",
            "version": "0.1.0",
            "description": "仅依据已观察接口生成，不是完整契约；使用前须人工核对。",
        },
        "paths": paths,
        "x-garden-inferred": True,
        "x-garden-project-revision": document["revision"],
        "x-garden-filters": document["filters"],
        "x-garden-excluded-observations": excluded,
        "x-garden-limitations": document["limitations"]
        + [
            "Paths 是字面路径，未猜测路径参数；各操作的 servers 与观察来源分别保留。",
            "未推断鉴权和正文 schema；缺失 security 不表示接口无需认证。",
        ],
    }
