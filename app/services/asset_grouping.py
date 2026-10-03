"""Versioned route families; never infer exact requests from redacted URLs."""

import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import parse_qsl, urlsplit

from app.schemas.assets import AssetGroup, AssetRecord, AssetRequestObservation, AssetVariant

RULE_VERSION = "route-v1"


def route_key(row: AssetRecord) -> str:
    """Public routing shape only. Preserve trailing slashes, escapes and key multiplicity."""
    try:
        parsed = urlsplit(row.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not row.method:
            raise ValueError
        # Catalog URLs have normalized origin and no credentials/values/fragments.
        material = [
            row.site,
            row.method.upper(),
            row.original_type,
            parsed.path or "/",
            sorted(name for name, _ in parse_qsl(parsed.query, keep_blank_values=True)),
        ]
        if row.route_url:
            material.append(["hash-v1", urlsplit(row.route_url).fragment])
        return json.dumps(material, ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        return "isolated:" + row.asset_id


def group_records(rows: list[AssetRecord], requests: dict[int, list[dict]]) -> list[AssetGroup]:
    buckets = defaultdict(list)
    for row in rows:
        buckets[route_key(row)].append(row)
    result = []
    for key, observations in buckets.items():
        observations.sort(key=lambda r: (r.record_type, r.record_id))
        first = observations[0]
        variant_buckets = defaultdict(list)
        all_known = True
        for row in observations:
            captures = requests.get(row.record_id, []) if row.record_type == "scan_asset" else []
            if not captures:
                all_known = False
                variant_buckets[("unknown", row.asset_id)].append((row, None))
            for capture in captures:
                match = re.fullmatch(r"v2:([0-9a-f]{40}):[0-9a-f]{40}", capture["fingerprint"])
                if match:
                    # Contexts are separate request environments, even for identical URL/body.
                    variant_key = ("known", capture["context_id"], match[1])
                else:
                    all_known = False
                    variant_key = ("unknown", capture["id"])
                variant_buckets[variant_key].append((row, capture))
        variants = []
        for variant_key, members in variant_buckets.items():
            request_ids = sorted({c["id"] for _, c in members if c})
            ids = sorted({r.asset_id for r, _ in members})
            # Do not expose request fingerprints: hashes may encode low-entropy secrets.
            suffix = f"request:{request_ids[0]}" if request_ids else ids[0]
            variants.append(
                AssetVariant(
                    variant_id=f"{':'.join(first.asset_id.split(':')[:2])}:variant:{suffix}",
                    identity_status="known" if variant_key[0] == "known" else "unknown",
                    observations=[request_observation(r, c) for r, c in members if c],
                    request_ids=request_ids,
                    observation_ids=ids,
                    contexts=sorted({r.context for r, _ in members}),
                    note=(
                        "按同一身份下的方法、完整 URL、请求体与请求头区分；"
                        "响应差异保留为独立请求记录。"
                        if variant_key[0] == "known"
                        else (
                            "历史记录缺少版本化请求指纹；此项是保留记录，"
                            "不能视为一个已确认的独立变体。"
                        )
                    ),
                )
            )
        variants.sort(key=lambda v: v.variant_id)
        namespace = first.asset_id.split(":", 2)[:2]
        identifier = ":".join(namespace) + ":group:" + hashlib.sha256(key.encode()).hexdigest()[:24]
        times = [r.last_seen for r in observations if r.last_seen]
        result.append(
            AssetGroup(
                validity_flags=[flag for row in observations for flag in row.validity.flags],
                asset_id=identifier,
                rule_version="hash-v1" if first.route_url else RULE_VERSION,
                kind=first.kind,
                url=first.route_url or first.url,
                method=first.method,
                site=first.site,
                title=first.title,
                last_seen=max(times, key=lambda t: t.isoformat()) if times else None,
                contexts=sorted({r.context for r in observations}),
                status_codes=sorted(
                    {s for r in observations for s in r.status_codes}
                    | {
                        o.status_code
                        for v in variants
                        for o in v.observations
                        if o.status_code is not None
                    }
                ),
                observation_count=len(observations),
                variant_count=len(variants) if all_known else None,
                variant_record_count=len(variants),
                variants=variants,
                observations=observations,
                evidence_ids=sorted({i for r in observations for i in r.evidence_ids or []}),
                request_ids=sorted({i for r in observations for i in r.request_ids or []}),
                grouping_reason="同一站点、方法、原始类型、精确路径与查询参数名集合（含重复次数）。参数值不参与主体归并，观察与请求记录完整保留。",
                limitations=(
                    "归并资产表示路由族，不代表业务等价。"
                    "旧采集已丢失的参数、响应或 hash 路由无法还原；"
                    "缺少请求记录时变体数量未知。仅在当前任务内归并。"
                ),
            )
        )
    return result


def request_observation(row, capture):
    # Metadata is additive and legacy/corrupt values remain unknown.
    from app.services.asset_catalog import safe_text

    response = capture.get("response")
    response = response if isinstance(response, dict) else {}
    code = response.get("status_code")
    return AssetRequestObservation(
        request_id=capture["id"],
        source_observation_id=row.asset_id,
        context=row.context,
        status_code=code if type(code) is int and 100 <= code <= 599 else None,
        content_type=safe_text(response.get("content_type")),
    )
