"""Conservative static JavaScript hints; never execute code or fetch targets.

This is a bounded lexer, not a JavaScript evaluator. It intentionally excludes
template strings, computed expressions, aliases, and relative child routes.
URLs are transport material; display redaction belongs to the caller.
"""

from app.services.discovery_html import MAX_BODY_CHARS, MAX_CANDIDATES, resolve_url

_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}
_MAX_TOKENS = 65_536


def _tokens(body: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(body) and len(tokens) < _MAX_TOKENS:
        char = body[i]
        if char.isspace():
            i += 1
            continue
        if body.startswith("//", i):
            end = body.find("\n", i + 2)
            i = len(body) if end < 0 else end + 1
            continue
        if body.startswith("/*", i):
            end = body.find("*/", i + 2)
            i = len(body) if end < 0 else end + 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            value: list[str] = []
            supported = quote != "`"
            i += 1
            while i < len(body) and body[i] != quote:
                if body[i] == "\\":
                    i += 1
                    if i == len(body):
                        break
                    escaped = body[i]
                    if escaped in {"\\", "'", '"', "/"}:
                        value.append(escaped)
                    elif escaped in {"u", "x"}:
                        width = 4 if escaped == "u" else 2
                        digits = body[i + 1 : i + 1 + width]
                        if len(digits) == width and all(
                            c in "0123456789abcdefABCDEF" for c in digits
                        ):
                            value.append(chr(int(digits, 16)))
                            i += width
                        else:
                            supported = False
                    else:
                        supported = False
                else:
                    if body[i] in "\r\n" and quote != "`":
                        supported = False
                    value.append(body[i])
                i += 1
            if i == len(body):
                break
            i += 1
            tokens.append(("string" if supported else "unsupported", "".join(value)))
            continue
        # Skip regex literals in expression-start positions. Ambiguous slash
        # constructs are deliberately not interpreted as URLs.
        if char == "/" and (not tokens or tokens[-1][1] in {"=", "(", ":", ",", "[", "return"}):
            i += 1
            in_class = False
            while i < len(body):
                if body[i] == "\\":
                    i += 2
                    continue
                if body[i] == "[":
                    in_class = True
                elif body[i] == "]":
                    in_class = False
                elif body[i] == "/" and not in_class:
                    i += 1
                    break
                i += 1
            tokens.append(("unsupported", "regex"))
            continue
        if char.isalpha() or char in "_$":
            end = i + 1
            while end < len(body) and (body[end].isalnum() or body[end] in "_$"):
                end += 1
            tokens.append(("name", body[i:end]))
            i = end
        else:
            tokens.append(("punct", char))
            i += 1
    return tokens


def _pairs(tokens: list[tuple[str, str]]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for index, token in enumerate(tokens):
        if token[0] != "punct":
            continue
        if token[1] in {"(", "[", "{"}:
            stack.append(index)
        elif token[1] in {")", "]", "}"}:
            if not stack or tokens[stack[-1]][1] != {")": "(", "]": "[", "}": "{"}[token[1]]:
                stack.clear()
                continue
            pairs[stack.pop()] = index
    return pairs


def _properties(
    tokens: list[tuple[str, str]], start: int, pairs: dict[int, int]
) -> dict[str, str | None]:
    """Read only direct, literal object fields, bounded to a small call config."""
    end = pairs.get(start, start)
    if end - start > 512 or tokens[start] != ("punct", "{"):
        return {}
    fields: dict[str, str | None] = {}
    i = start + 1
    while i < end:
        if tokens[i] in {("punct", "."), ("punct", "[")}:
            # Spread/computed fields can overwrite both the URL and method.
            return {"method": None}
        if tokens[i] == ("name", "method") and tokens[i + 1] in {("punct", ","), ("punct", "}")}:
            fields["method"] = None
        if i + 2 < end and tokens[i][0] in {"name", "string"} and tokens[i + 1] == ("punct", ":"):
            key = tokens[i][1]
            value = tokens[i + 2]
            fields[key] = (
                value[1]
                if value[0] == "string" and tokens[i + 3] in {("punct", ","), ("punct", "}")}
                else None
            )
            i += 2
        i = pairs.get(i, i) + 1
    return fields


def extract_js(base_url: str, body: str, limit: int = 2000) -> list[dict]:
    """Extract static fetch/XHR/axios targets and absolute declared route paths."""
    limit = min(MAX_CANDIDATES, max(0, limit))
    if not limit or len(body) > MAX_BODY_CHARS:
        return []
    tokens = _tokens(body)
    pairs = _pairs(tokens)
    tokens.extend([("end", "")] * 4)
    items: list[dict] = []
    seen: set[tuple] = set()
    xhr_names: set[str] = set()
    route_ends: list[int] = []

    def add(value: str, method: str | None, call_kind: str, *, route: bool = False) -> None:
        url = resolve_url(base_url, value)
        if not url or (method is not None and method.upper() not in _METHODS):
            return
        method = method.upper() if method else None
        key = (url, method, call_kind)
        if key in seen or len(items) >= limit:
            return
        seen.add(key)
        items.append(
            {
                "url": url,
                "asset_type": "page" if route else "api",
                "method": method,
                "source_kind": "js_literal",
                "source_url": base_url,
                "auto_visit": False,
                "call_kind": call_kind,
            }
        )

    for i, token in enumerate(tokens):
        if len(items) >= limit:
            break
        while route_ends and route_ends[-1] < i:
            route_ends.pop()
        if i + 4 >= len(tokens):
            continue
        if token[0] == "name" and tokens[i + 1 : i + 4] == [
            ("punct", "="),
            ("name", "new"),
            ("name", "XMLHttpRequest"),
        ]:
            xhr_names.add(token[1])
        if (
            token in {("name", "routes"), ("string", "routes")}
            and tokens[i + 1] in {("punct", "="), ("punct", ":")}
            and tokens[i + 2] == ("punct", "[")
            and i + 2 in pairs
        ):
            route_ends.append(pairs[i + 2])
        if (
            token
            in {
                ("name", "createBrowserRouter"),
                ("name", "createHashRouter"),
                ("name", "createMemoryRouter"),
            }
            and tokens[i + 1 : i + 3] == [("punct", "("), ("punct", "[")]
            and i + 2 in pairs
        ):
            route_ends.append(pairs[i + 2])
        if (
            route_ends
            and token in {("name", "path"), ("string", "path")}
            and tokens[i + 1] == ("punct", ":")
        ):
            value = tokens[i + 2]
            if (
                value[0] == "string"
                and value[1].startswith("/")
                and tokens[i + 3] in {("punct", ","), ("punct", "}")}
            ):
                add(value[1], "GET", "route", route=True)
        call = None
        start = i + 1
        if token == ("name", "fetch") and (i == 0 or tokens[i - 1] != ("punct", ".")):
            call = "fetch"
        elif token == ("name", "axios"):
            call = "axios"
            if tokens[i + 1] == ("punct", ".") and tokens[i + 2][0] == "name":
                call += "." + tokens[i + 2][1]
                start = i + 3
        elif (
            token[0] == "name"
            and token[1] in xhr_names
            and tokens[i + 1 : i + 3] == [("punct", "."), ("name", "open")]
        ):
            call = "xhr.open"
            start = i + 3
        if not call or tokens[start] != ("punct", "(") or start not in pairs:
            continue
        end = pairs[start]
        if end - start > 512 or start + 2 > end:
            continue
        arg = tokens[start + 1]
        if call in {"axios", "axios.request"}:
            fields = _properties(tokens, start + 1, pairs)
            if fields.get("url"):
                add(fields["url"], fields.get("method", "GET"), call)
            continue
        if call == "xhr.open":
            if (
                start + 4 <= end
                and arg[0] == "string"
                and tokens[start + 2] == ("punct", ",")
                and tokens[start + 3][0] == "string"
                and tokens[start + 4] in {("punct", ","), ("punct", ")")}
            ):
                add(tokens[start + 3][1], arg[1], call)
            continue
        if arg[0] != "string" or tokens[start + 2] not in {("punct", ","), ("punct", ")")}:
            continue
        method = "GET"
        if call == "fetch" and tokens[start + 2] == ("punct", ","):
            if tokens[start + 3] != ("punct", "{"):
                method = None
            else:
                method = _properties(tokens, start + 3, pairs).get("method", "GET")
        elif call.startswith("axios."):
            method = call.split(".", 1)[1].upper()
        add(arg[1], method, call)
    return items
