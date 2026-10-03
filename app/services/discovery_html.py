"""Bounded, network-free discovery of declared HTML targets.

Returned URLs are transport material. Callers must redact them before display and
apply their own scope/network policy before requesting any candidate.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BODY_CHARS = 262_144
MAX_CANDIDATES = 10_000
_SRCSET_URL = re.compile(r"[\s,]*([^\s]+)")


def resolve_url(base_url: str, value: str) -> str | None:
    """Resolve an HTTP target without credentials or document fragments."""
    value = value.strip()
    if not value or any(ord(char) < 32 for char in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(urljoin(base_url, value))
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        port = parts.port
        host = parts.hostname.lower()
        host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
        if port is not None and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return None


class _BaseParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.base_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base" and self.base_url is None:
            self.base_url = resolve_url(self.source_url, dict(attrs).get("href") or "")


class _DiscoveryParser(HTMLParser):
    def __init__(self, base_url: str, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = base_url
        self.base_url = base_url
        self.limit = min(MAX_CANDIDATES, max(0, limit))
        self.items: list[dict] = []
        self.seen: set[tuple] = set()
        self.title_parts: list[str] = []
        self.in_title = False
        self.has_base = False
        self.form_method = "GET"

    def add(
        self,
        value: str,
        source_kind: str,
        hint: str = "page",
        *,
        method: str = "GET",
        auto_visit: bool = True,
    ) -> None:
        from app.services.scan_network import classify_asset_type

        value = value.strip()
        if value.startswith("#") and not value.startswith(("#/", "#!/")):
            return
        url = resolve_url(self.base_url, value)
        if url is None or len(self.items) >= self.limit:
            return
        fragment = urlsplit(value).fragment
        route_url = None
        if fragment.startswith(("/", "!/")):
            route_url = url + "#" + fragment
            source_kind = "hash_route"
            auto_visit = False
        key = (url, source_kind, method, route_url)
        if key in self.seen:
            return
        self.seen.add(key)
        self.items.append(
            {
                "url": url,
                "asset_type": classify_asset_type(url, hint=hint),
                "method": method,
                "source_kind": source_kind,
                "source_url": self.source_url,
                "auto_visit": auto_visit,
            }
        )
        if route_url:
            self.items[-1]["route_url"] = route_url

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "base" and not self.has_base:
            base = resolve_url(self.source_url, values.get("href") or "")
            if base:
                self.base_url = base
                self.has_base = True
        for name, attr, kind, hint in (
            ("a", "href", "html_link", "page"),
            ("area", "href", "html_link", "page"),
            ("iframe", "src", "iframe", "page"),
            ("script", "src", "resource", "script"),
            ("img", "src", "resource", "image"),
            ("source", "src", "resource", "document"),
            ("audio", "src", "resource", "document"),
            ("video", "src", "resource", "document"),
            ("video", "poster", "resource", "image"),
        ):
            if tag == name:
                self.add(values.get(attr) or "", kind, hint)
        if tag == "link":
            relations = (values.get("rel") or "").lower().split()
            if set(relations) & {
                "stylesheet",
                "preload",
                "modulepreload",
                "icon",
                "apple-touch-icon",
            }:
                hint = "stylesheet" if "stylesheet" in relations else "document"
                if "icon" in relations or "apple-touch-icon" in relations:
                    hint = "image"
                hint = {"script": "script", "style": "stylesheet", "image": "image"}.get(
                    values.get("as") or "", hint
                )
                self.add(values.get("href") or "", "resource", hint)
        if tag in {"img", "source"}:
            # A URL token can contain commas (notably data URLs); descriptors end
            # at the next comma. Avoid splitting a data URL into relative paths.
            srcset = values.get("srcset") or ""
            position = 0
            while position < len(srcset) and len(self.items) < self.limit:
                match = _SRCSET_URL.match(srcset, position)
                if not match:
                    break
                token = match.group(1)
                position = match.end()
                self.add(token.rstrip(","), "resource", "image")
                if not token.endswith(","):
                    comma = srcset.find(",", position)
                    position = len(srcset) if comma < 0 else comma + 1
        if tag == "form":
            self.form_method = (values.get("method") or "GET").upper()
            if self.form_method not in {"GET", "POST", "DIALOG"}:
                self.form_method = "GET"
            self.add(
                values.get("action") or self.source_url,
                "form_action",
                method=self.form_method,
                auto_visit=False,
            )
        if tag in {"button", "input"} and values.get("formaction"):
            method = (values.get("formmethod") or self.form_method).upper()
            self.add(values["formaction"], "form_action", method=method, auto_visit=False)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag == "form":
            self.form_method = "GET"

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def parse_html(base_url: str, body: str, limit: int = 2000) -> tuple[str | None, list[dict]]:
    """Return document title and bounded discoveries relative to its final URL."""
    body = body[:MAX_BODY_CHARS]
    base_parser = _BaseParser(base_url)
    base_parser.feed(body)
    base_parser.close()
    parser = _DiscoveryParser(base_url, limit)
    parser.base_url = base_parser.base_url or base_url
    parser.has_base = True
    parser.feed(body)
    parser.close()
    title = " ".join("".join(parser.title_parts).split()) or None
    return title, parser.items
