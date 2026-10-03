"""Integrate bounded sources with the existing quick scan and persistence."""

from collections import deque
from urllib.parse import urljoin

from sqlalchemy import select

from app.core.errors import InputValidationError, TargetPolicyError
from app.models.scan_run import ScanAsset
from app.schemas.scan import DiscoveredAsset
from app.services.discovery import DiscoveryLedger, origin
from app.services.discovery_sources import parse_seed_input, parse_sitemap
from app.services.scan_network import FetchError
from app.services.session_storage import SessionStorageService


class ScanDiscovery:
    def __init__(self, pipeline, session, run, options, deadline, entry_url):
        self.pipeline, self.session, self.run = pipeline, session, run
        self.options, self.deadline = options, deadline
        self.ledger = DiscoveryLedger(entry_url, options.max_candidates)
        self.entry_url = entry_url
        self.private = (
            SessionStorageService().read_payload(run.discovery_input_ref)
            if run.discovery_input_ref
            else {}
        )
        self.source_incomplete = False

    def save(self, complete=False):
        self.run.asset_metadata = self.ledger.snapshot(
            complete=complete and not self.source_incomplete
        )
        for asset in self.session.scalars(
            select(ScanAsset).where(ScanAsset.scan_run_id == self.run.id)
        ):
            matches = [
                i
                for i in self.ledger.items.values()
                if i["observed"]
                and i.get("response_url", i["url"]) == asset.url.split("#", 1)[0]
                and i["method"] == (asset.method or "GET")
            ]
            if matches:
                sources = []
                for item in matches:
                    for source in item["sources"]:
                        if source not in sources:
                            sources.append(source)
                attrs = dict(asset.attributes or {})
                attrs["discovery"] = {
                    "version": 1,
                    "sources": sources[:20],
                    "source_count": None
                    if any(i["sources_truncated"] for i in matches)
                    else len(sources),
                    "truncated": len(sources) > 20 or any(i["sources_truncated"] for i in matches),
                }
                asset.attributes = attrs
        self.session.flush()

    def warning(self, reason):
        self.source_incomplete = True
        self.pipeline._record_failure(
            self.session,
            self.run,
            stage="collect",
            code="discovery_incomplete",
            message=f"发现来源未完整处理：{reason}。已有结果保留。",
            url=None,
            retryable=False,
            attempt=1,
        )

    def extra_sources(self):
        text = self.private.get("seed_input", "")
        if text:
            for item in parse_seed_input(text, self.options.seed_format, base_url=self.entry_url):
                yield DiscoveredAsset.model_validate(item)
        if not self.options.sitemap_enabled:
            return
        queue = deque(
            [(self.private.get("sitemap_url") or urljoin(self.entry_url, "/sitemap.xml"), 0)]
        )
        seen = set()
        while queue:
            url, depth = queue.popleft()
            if url in seen:
                continue
            if (
                len(seen) >= self.options.max_sitemap_documents
                or depth > self.options.max_sitemap_depth
            ):
                self.warning("sitemap 预算上限")
                break
            seen.add(url)
            if origin(url) != origin(self.entry_url):
                self.ledger.add(
                    DiscoveredAsset(
                        url=url, asset_type="document", source_kind="sitemap", auto_visit=False
                    )
                )
                self.warning("sitemap 索引超出同源范围")
                continue
            self.ledger.add(DiscoveredAsset(url=url, asset_type="document", source_kind="sitemap"))
            try:
                response = self.pipeline.gateway.fetch(
                    url,
                    self.options,
                    expected_origin=self.pipeline._origin(self.entry_url),
                    before_request=lambda: self.pipeline._guard_network_request(
                        self.session, self.run.id, self.deadline
                    ),
                    on_request_attempt=lambda attempted, source_url=url: self.ledger.attempted(
                        source_url
                    ),
                )
                self.pipeline._persist_fetch(
                    self.session, self.run, response, depth=depth, asset_type_hint="document"
                )
                self.ledger.observed(
                    url, response_url=response.final_url, aliases=response.redirects
                )
                if response.body_truncated or response.status_code != 200:
                    raise InputValidationError("Sitemap response incomplete.")
                candidates, indexes = parse_sitemap(
                    response.final_url, response.body_text, limit=self.options.max_candidates
                )
                for item in candidates:
                    yield DiscoveredAsset.model_validate(item)
                queue.extend((child, depth + 1) for child in indexes)
            except (InputValidationError, TargetPolicyError, FetchError):
                self.warning("sitemap 格式、范围或响应错误")
            self.save()
            self.session.commit()

    def script_sources(self, response):
        if not self.options.js_enabled or response.body_truncated or not response.body_is_text:
            return []
        if "javascript" not in (response.content_type or "") and not response.final_url.split(
            "?", 1
        )[0].endswith(".js"):
            return []
        from app.services.discovery_js import extract_js

        items = extract_js(
            response.final_url, response.body_text, limit=self.options.max_candidates
        )
        if len(items) >= self.options.max_candidates:
            self.warning("JS 线索达到解析上限，无法确认是否仍有遗漏")
        return [DiscoveredAsset.model_validate(i) for i in items]

    def browser_collect(self):
        from datetime import datetime, timezone

        from app.models.scan_context import ScanContext
        from app.services.discovery import safe_route
        from app.services.discovery_browser import AnonymousDiscoveryBrowser

        def candidate(item):
            if item.get("attempt_only"):
                self.ledger.attempted(item["url"], item.get("method", "GET"))
                self.save()
                return
            route = safe_route(item.get("route_url"))
            if item.get("route_observed") and route:
                view = DiscoveredAsset(
                    url=item["url"],
                    route_url=item["route_url"],
                    asset_type="page",
                    source_kind="hash_route",
                    auto_visit=False,
                )
                self.ledger.add(view)
                key = self.ledger.key(view.url, view.method, view.route_url)
                if key in self.ledger.items:
                    self.ledger.items[key]["observed"] = True
                    self.ledger.items[key]["reason"] = "route_view_observed"
                context = self.session.scalar(
                    select(ScanContext).where(
                        ScanContext.scan_run_id == self.run.id, ScanContext.kind == "anonymous"
                    )
                )
                asset = self.session.scalar(
                    select(ScanAsset).where(
                        ScanAsset.scan_run_id == self.run.id,
                        ScanAsset.url == route,
                        ScanAsset.context_id == context.id,
                    )
                )
                if asset is None:
                    self.session.add(
                        ScanAsset(
                            scan_run_id=self.run.id,
                            context_id=context.id,
                            identity_key="hash-v1:" + route,
                            url=route,
                            method="GET",
                            asset_type="page",
                            discovered_at=datetime.now(timezone.utc),
                            attributes={
                                "route_url": route,
                                "discovery": {
                                    "version": 1,
                                    "sources": [{"kind": "hash_route", "url": None}],
                                    "source_count": 1,
                                },
                            },
                        )
                    )
            else:
                self.ledger.add(item)
            self.save()

        def response(result, source_route):
            # Route attribution is navigation-window context, not proof of initiator.
            document_url = result.final_url
            if self.ledger.key(document_url, result.method) not in self.ledger.items:
                self.ledger.add(
                    DiscoveredAsset(
                        url=document_url,
                        asset_type="endpoint" if "json" in (result.content_type or "") else "page",
                        method=result.method,
                        source_kind="browser_request",
                        source_url=None,
                    )
                )
            self.ledger.observed(document_url, result.method)
            self.pipeline._persist_fetch(
                self.session,
                self.run,
                result,
                depth=0,
                asset_type_hint="endpoint" if "json" in (result.content_type or "") else None,
            )
            for child in self.script_sources(result):
                candidate(child.model_dump())
            self.save()
            self.session.commit()

        self.ledger.add(DiscoveredAsset(url=self.entry_url, asset_type="page", source_kind="entry"))
        seeds = []
        for item in self.extra_sources():
            if self.ledger.add(item) and item.auto_visit:
                seeds.append(item.url)
        self.save()
        self.session.commit()
        try:
            result = AnonymousDiscoveryBrowser(self.pipeline.policy).collect(
                self.entry_url,
                self.options,
                lambda: self.pipeline._guard_network_request(
                    self.session, self.run.id, self.deadline
                ),
                response,
                candidate,
                seeds,
                on_attempt=lambda url, method: candidate(
                    {"url": url, "method": method, "attempt_only": True}
                ),
            )
            if result["stopped_reason"]:
                self.warning(result["stopped_reason"])
            self.save(complete=True)
            self.session.commit()
            return None, "Anonymous browser discovery completed within the recorded bounds."
        except Exception:
            self.save(complete=False)
            self.session.commit()
            raise
