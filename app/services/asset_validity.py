"""Passive, explainable hints. No target probes or protected payload reads."""

import re
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.schemas.assets import AssetValidity, ValidityFlag
from app.services.coverage_identity import stable_response_signature

LABELS = {
    "login_page": "疑似登录页面",
    "suspected_login_fallback": "疑似登录页回退",
    "suspected_soft_404": "疑似软 404",
    "uniform_response": "疑似统一响应",
    "uniform_error_response": "疑似统一错误响应",
    "redirect_alias": "已观察重定向别名",
}


class _HTMLTraits(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = []
        self.password = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "input" and (dict(attrs).get("type") or "").lower() == "password":
            self.password = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def capture_traits(body, content_type, *, complete=True):
    """Store booleans and private signatures, never body snippets or exact URLs."""
    body = body if isinstance(body, str) else ""
    media = (content_type or "").split(";", 1)[0].strip().lower()
    parser = _HTMLTraits()
    if media in {"text/html", "application/xhtml+xml"} and complete:
        parser.feed(body)
    title = " ".join(parser.title).strip().casefold()
    return {
        "version": 1,
        "signature": stable_response_signature(body, media) if complete and body.strip() else None,
        "login_page": bool(
            parser.password and re.search(r"\b(log\s*in|sign\s*in|login)\b|登录|登入", title)
        ),
        "not_found_title": bool(
            re.fullmatch(
                r"(?:404\s*[-:|]?\s*)?(?:not found|page not found|页面不存在|页面未找到)|404", title
            )
        ),
    }


def annotate_validity(rows, signals):
    """Compare only same-origin, same-context complete response signatures."""
    by_signature = defaultdict(list)
    by_id = {r.asset_id: r for r in rows}
    for row in rows:
        # Preserve observed statuses from associated evidence and request snapshots.
        row.status_codes = sorted(
            set(row.status_codes)
            | {
                code
                for signal in signals.get(row.asset_id, [])
                for code in signal.get("status_codes", [])
                if type(code) is int and 100 <= code <= 599
            }
        )
        row.observation = "response_observed" if row.status_codes else "unknown"
        row.validity = AssetValidity(
            verification="response_observed" if row.status_codes else "unknown",
            access="restricted" if set(row.status_codes) & {401, 403} else "unknown",
        )
        context = row.context if row.context != "unknown" else row.asset_id
        for signal in signals.get(row.asset_id, []):
            traits = signal.get("traits")
            if not isinstance(traits, dict) or traits.get("version") != 1:
                continue
            signature = traits.get("signature")
            if row.site and isinstance(signature, str) and re.fullmatch("[0-9a-f]{64}", signature):
                by_signature[(row.site, context, signature)].append((row.asset_id, signal))
    for row in rows:
        flags = {}
        aliases = set()

        def add(code, reason, related=(), request_ids=(), flags=flags):
            previous = flags.get(code)
            flags[code] = ValidityFlag(
                code=code,
                label=LABELS[code],
                reason=reason,
                related_asset_ids=sorted(
                    set(related) | set(previous.related_asset_ids if previous else [])
                )[:20],
                request_ids=sorted(
                    set(request_ids) | set(previous.request_ids if previous else [])
                ),
            )

        for signal in signals.get(row.asset_id, []):
            traits = signal.get("traits")
            traits = traits if isinstance(traits, dict) and traits.get("version") == 1 else {}
            codes = signal.get("status_codes", row.status_codes)
            codes = [c for c in codes if type(c) is int and 100 <= c <= 599]
            requests = signal.get("request_ids", [])
            if traits.get("signature") or traits.get("login_page") or traits.get("not_found_title"):
                row.validity.assessment = "assessed"
            if set(codes) & {401, 403}:
                row.validity.access = "restricted"
            redirects = signal.get("redirects", [])
            from app.services.asset_catalog import safe_url

            redirected = False
            for redirect in redirects if isinstance(redirects, list) else []:
                if not isinstance(redirect, dict):
                    continue
                source = safe_url(redirect.get("requested_url"))
                final = safe_url(redirect.get("final_url"))
                if (
                    source.startswith(("http://", "https://"))
                    and final.startswith(("http://", "https://"))
                    and source != final
                ):
                    aliases.add(source)
                    redirected = True
                    row.validity.assessment = "assessed"
                    add(
                        "redirect_alias",
                        "已记录请求入口与最终 URL 不同；仅作为别名关联，不删除原始观察。",
                        request_ids=requests,
                    )
            if traits.get("login_page") is True and codes:
                add(
                    "login_page",
                    "响应同时包含登录标题和密码输入框；仅为页面特征，不确认会话失效。",
                    request_ids=requests,
                )
                if redirected:
                    add(
                        "suspected_login_fallback",
                        "已记录重定向，并在最终响应观察到登录页面特征。",
                        request_ids=requests,
                    )
            context = row.context if row.context != "unknown" else row.asset_id
            signature = traits.get("signature")
            matches = (
                by_signature.get((row.site, context, signature), [])
                if isinstance(signature, str)
                else []
            )
            same_status = [
                (i, s)
                for i, s in matches
                if set(s.get("status_codes", by_id[i].status_codes)) & set(codes)
            ]
            paths = {urlsplit(by_id[i].url).path for i, _ in same_status}
            if len(paths) >= 3:
                code = (
                    "uniform_error_response"
                    if codes and all(c >= 400 for c in codes)
                    else "uniform_response"
                )
                add(
                    code,
                    f"同一站点及身份的 {len(paths)} 个路径出现相同完整响应指纹和相同状态码；"
                    "SPA 公共外壳也可能如此，不能据此确认无效。",
                    [i for i, _ in same_status if i != row.asset_id],
                    requests,
                )
            if (
                traits.get("login_page") is True
                and len({urlsplit(by_id[i].url).path for i, _ in matches}) >= 2
            ):
                add(
                    "suspected_login_fallback",
                    "同一身份下多个路径返回相同登录页面；需要人工核实业务含义。",
                    [i for i, _ in matches if i != row.asset_id],
                    requests,
                )
            not_found = [
                (i, s)
                for i, s in matches
                if set(s.get("status_codes", by_id[i].status_codes)) & {404, 410}
            ]
            if any(200 <= c < 300 for c in codes) and (
                traits.get("not_found_title") is True or not_found
            ):
                add(
                    "suspected_soft_404",
                    "成功状态码响应含明确的未找到页面标题，"
                    "或与同一身份已观察到的 404/410 完整响应相同；未额外探测。",
                    [i for i, _ in not_found if i != row.asset_id],
                    requests,
                )
        row.validity.flags = [flags[k] for k in sorted(flags)]
        row.validity.aliases = sorted(aliases)
    return rows
