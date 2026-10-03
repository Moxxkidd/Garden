"""Bounded per-run discovery accounting, independent of transport and public views."""

from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schemas.scan import DiscoveredAsset
from app.services.coverage_identity import redacted_observed_url

SOURCE_KINDS = {
    "entry": "入口",
    "html_link": "页面链接",
    "iframe": "内嵌页面",
    "resource": "资源引用",
    "form_action": "表单声明",
    "sitemap": "站点地图",
    "browser_navigation": "浏览器导航",
    "browser_request": "浏览器请求",
    "hash_route": "前端路由",
    "js_literal": "JS 线索",
    "url_import": "URL 导入",
    "openapi_import": "OpenAPI 声明",
    "unknown": "来源未知",
}


def origin(url):
    parsed = urlsplit(url)
    port = parsed.port
    default = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme.lower()}://{host}" + (f":{port}" if port and not default else "")


def safe_route(url):
    """Retain route shape; never expose hash-route query values or ordinary fragments."""
    if not url:
        return None
    parsed = urlsplit(url)
    fragment = parsed.fragment
    if not fragment.startswith(("/", "!/")):
        return None
    route = urlsplit(fragment.lstrip("!"))
    query = urlencode(
        [(name, "[REDACTED]") for name, _ in parse_qsl(route.query, keep_blank_values=True)]
    )
    return (
        redacted_observed_url(url)
        + "#"
        + ("!" if fragment.startswith("!") else "")
        + urlunsplit(("", "", route.path, query, ""))
    )


class DiscoveryLedger:
    def __init__(self, entry_url, limit):
        self.origin = origin(entry_url)
        self.limit = limit
        self.items = {}
        self.response_aliases = {}
        self.stats = defaultdict(
            lambda: dict(
                discovered=0,
                new_candidates=0,
                duplicate_candidates=0,
                request_attempts=0,
                response_observed=0,
                skipped=0,
            )
        )
        self.truncated = False

    @staticmethod
    def key(url, method="GET", route_url=None):
        return (url.split("#", 1)[0], (method or "UNKNOWN").upper(), route_url or safe_route(url))

    def add(self, candidate):
        if isinstance(candidate, dict):
            candidate = DiscoveredAsset.model_validate(candidate)
        kind = candidate.source_kind if candidate.source_kind in SOURCE_KINDS else "unknown"
        stats = self.stats[kind]
        stats["discovered"] += 1
        key = self.key(candidate.url, candidate.method, candidate.route_url)
        if key not in self.items and len(self.items) >= self.limit:
            self.truncated = True
            stats["skipped"] += 1
            return False
        source = {
            "kind": kind,
            "url": redacted_observed_url(candidate.source_url) if candidate.source_url else None,
            "index": candidate.source_index,
        }
        existing = self.items.get(key)
        if existing:
            stats["duplicate_candidates"] += 1
            if source not in existing["sources"]:
                if len(existing["sources"]) < 20:
                    existing["source_count"] += 1
                    existing["sources"].append(source)
                else:
                    existing["sources_truncated"] = True
                    existing["source_count"] = None
            if not existing["observed"] and candidate.skipped_reason in {
                "max_candidates",
                "max_pages",
                "max_resources",
                "max_browser_requests",
                "max_redirects",
                "non_readonly_method",
                "max_depth",
                "navigation_error",
            }:
                existing["reason"] = candidate.skipped_reason
            # A genuine link can authorize GET even if a declaration was seen first.
            if candidate.auto_visit and existing["reason"] == "declaration_only":
                existing["reason"] = "pending"
            return existing["reason"] == "pending"
        outside = origin(candidate.url) != self.origin
        reason = (
            "outside_scope"
            if outside
            else ("pending" if candidate.auto_visit else "declaration_only")
        )
        if not outside and candidate.skipped_reason in {
            "max_candidates",
            "max_pages",
            "max_resources",
            "max_browser_requests",
            "max_redirects",
            "non_readonly_method",
            "max_depth",
            "navigation_error",
        }:
            reason = candidate.skipped_reason
        self.items[key] = dict(
            url=redacted_observed_url(candidate.url),
            asset_type=candidate.asset_type,
            method=(candidate.method or "UNKNOWN").upper(),
            route_url=safe_route(candidate.route_url or candidate.url),
            sources=[source],
            source_count=1,
            sources_truncated=False,
            source_kind=kind,
            reason=reason,
            observed=False,
        )
        if key in self.response_aliases:
            self.items[key].update(
                observed=True, reason="redirect_target", response_url=self.response_aliases[key]
            )
            reason = "redirect_target"
        stats["new_candidates"] += 1
        if reason != "pending":
            stats["skipped"] += 1
        return reason == "pending"

    def attempted(self, url, method="GET", route_url=None):
        item = self.items.get(self.key(url, method, route_url))
        if item:
            self.stats[item["source_kind"]]["request_attempts"] += 1
            item["reason"] = "request_failed"

    def observed(self, url, method="GET", route_url=None, response_url=None, aliases=()):
        destination = redacted_observed_url(response_url or url)
        for alias_url in [response_url or url, *aliases]:
            alias_key = self.key(alias_url, method, route_url)
            self.response_aliases[alias_key] = destination
            alias = self.items.get(alias_key)
            if alias and alias_key != self.key(url, method, route_url):
                alias.update(observed=True, reason="redirect_target", response_url=destination)
        item = self.items.get(self.key(url, method, route_url))
        if item and not item["observed"]:
            item["observed"] = True
            item["response_url"] = redacted_observed_url(response_url or url)
            if response_url:
                alias = self.items.get(self.key(response_url, method, route_url))
                if alias and alias is not item:
                    alias["observed"] = True
                    alias["reason"] = "redirect_target"
            item["reason"] = "response_observed"
            self.stats[item["source_kind"]]["response_observed"] += 1

    def snapshot(self, *, complete):
        items = list(self.items.values())
        return dict(
            version=2,
            complete=complete and not self.truncated,
            truncated=self.truncated,
            stats=dict(self.stats),
            candidates=[dict(i) for i in items if not i["observed"]],
            observations=[dict(i) for i in items if i["observed"]],
        )
