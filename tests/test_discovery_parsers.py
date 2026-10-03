from app.services.discovery_html import parse_html


def test_html_final_document_base_and_distinct_sources():
    title, items = parse_html(
        "https://example.test/redirected/page",
        '<title>Example &amp; title</title><base href="../assets/">'
        '<a href="shared">Page</a><iframe src="shared"></iframe>'
        '<a href="shared">Duplicate</a><script src="app.js"></script>',
    )
    assert title == "Example & title"
    assert [(item["url"], item["source_kind"]) for item in items] == [
        ("https://example.test/assets/shared", "html_link"),
        ("https://example.test/assets/shared", "iframe"),
        ("https://example.test/assets/app.js", "resource"),
    ]
    assert all(item["source_url"] == "https://example.test/redirected/page" for item in items)
    assert all(item["auto_visit"] and item["method"] == "GET" for item in items)


def test_html_resources_forms_routes_and_unsafe_values():
    _, items = parse_html(
        "https://example.test/dir/page",
        '<base href="javascript:bad"><base href="/assets/">'
        '<area href="map"><link rel="stylesheet" href="style.css">'
        '<link rel="canonical" href="ignored"><link rel="preload" as="font" href="f.woff">'
        '<img src="a.png" srcset="data:image/png;base64,AAAA 1x, b.png 2x, c.png 3x">'
        '<source src="movie.mp4" srcset="wide.webp 2x">'
        '<audio src="audio.ogg"></audio><video src="movie.mp4" poster="poster.png"></video>'
        '<form action="submit" method="post"><button formaction="preview">Go</button>'
        '<input type="submit" formaction="confirm" formmethod="GET"></form>'
        '<a href="#section">Anchor</a><a href="#/one?q=value">One</a>'
        '<a href="#!/two">Two</a><a href="mailto:a@example.test">Mail</a>'
        '<a href="https://u:secret@example.test/">Credentials</a>'
        '<a href="https://[broken/">Broken</a>',
    )
    by_path = {
        item["url"].split("example.test", 1)[1]: item for item in items if "route_url" not in item
    }
    assert set(by_path) == {
        "/assets/map",
        "/assets/style.css",
        "/assets/f.woff",
        "/assets/a.png",
        "/assets/b.png",
        "/assets/c.png",
        "/assets/movie.mp4",
        "/assets/wide.webp",
        "/assets/audio.ogg",
        "/assets/poster.png",
        "/assets/submit",
        "/assets/preview",
        "/assets/confirm",
    }
    for path, method in [("submit", "POST"), ("preview", "POST"), ("confirm", "GET")]:
        item = by_path[f"/assets/{path}"]
        assert (item["method"], item["source_kind"], item["auto_visit"]) == (
            method,
            "form_action",
            False,
        )
    routes = [item for item in items if item["source_kind"] == "hash_route"]
    assert [item["route_url"] for item in routes] == [
        "https://example.test/assets/#/one?q=value",
        "https://example.test/assets/#!/two",
    ]
    assert all(not item["auto_visit"] for item in routes)


def test_html_budget_malformed_input_and_duplicate_forms():
    _, items = parse_html(
        "https://example.test/",
        '<form action="/same" method="post">'
        '</form><a href="/same">Link</a><form action="/same" method="get"></form>',
    )
    assert len(items) == 3
    _, items = parse_html("https://example.test/", '<a href="/first"><a href="/second">', limit=1)
    assert [item["url"] for item in items] == ["https://example.test/first"]
    assert parse_html("https://example.test/", '<a href="/first">', limit=-1)[1] == []
    assert parse_html("https://example.test/", "x" * 262_144 + '<a href="/late">')[1] == []
    assert parse_html("https://example.test/", '<a href="unfinished')[1] == []


def test_js_static_calls_methods_and_route_declarations():
    from app.services.discovery_js import extract_js

    items = extract_js(
        "https://example.test/static/app.js",
        """
        fetch("../api/users");
        fetch('/api/save', {method: 'POST', headers: {Authorization: 'SECRET'}});
        const req = new XMLHttpRequest(); req.open("PATCH", "/api/update", true);
        axios.get('/api/list'); axios.delete('/api/delete');
        axios({url: '/api/config', method: 'put'});
        const routes = [{path: '/home'}, {path: '/users/:id', children: [{path: 'details'}]}];
    """,
    )
    assert [(item["url"], item["method"]) for item in items] == [
        ("https://example.test/api/users", "GET"),
        ("https://example.test/api/save", "POST"),
        ("https://example.test/api/update", "PATCH"),
        ("https://example.test/api/list", "GET"),
        ("https://example.test/api/delete", "DELETE"),
        ("https://example.test/api/config", "PUT"),
        ("https://example.test/home", "GET"),
        ("https://example.test/users/:id", "GET"),
    ]
    assert all(item["source_kind"] == "js_literal" and not item["auto_visit"] for item in items)
    assert all(item["source_url"] == "https://example.test/static/app.js" for item in items)
    assert "SECRET" not in repr(items)
    assert [item["call_kind"] for item in items[:4]] == ["fetch", "fetch", "xhr.open", "axios.get"]


def test_js_does_not_guess_dynamic_calls_or_scan_comments_and_strings():
    from app.services.discovery_js import extract_js

    assert (
        extract_js(
            "https://example.test/app.js",
            r"""
        // fetch('/comment');
        /* axios.get('/comment2'); */
        const text = "fetch('/quoted')";
        const regex = /fetch\("\/regex"\)/;
        fetch('/prefix/' + user); fetch(`/users/${id}`); fetch(endpoint);
        axios.get('/prefix/' + id); req.open('GET', '/prefix/' + id);
        const config = {path: '/not-a-route'};
        const routes = [{path: '/prefix/' + id}];
        fetch('javascript:alert(1)'); fetch('https://user:password@example.test/secret');
    """,
        )
        == []
    )


def test_js_malformed_and_bounded_input():
    from app.services.discovery_js import extract_js

    assert extract_js("https://example.test/app.js", "fetch('/unfinished") == []
    assert extract_js("https://example.test/app.js", "x" * 262_144 + ";fetch('/late')") == []
    assert (
        extract_js("https://example.test/app.js", "fetch('/one');fetch('/two');", limit=1)[0]["url"]
        == "https://example.test/one"
    )
    assert extract_js("https://example.test/app.js", "fetch('/one')", limit=0) == []


def test_html_base_applies_to_links_before_base_and_empty_forms_use_document():
    _, items = parse_html(
        "https://example.test/final/page",
        '<a href="first">First</a><base href="/root/"><form method="post"></form>',
    )
    assert [(item["url"], item["method"]) for item in items] == [
        ("https://example.test/root/first", "GET"),
        ("https://example.test/final/page", "POST"),
    ]


def test_js_unknown_options_do_not_invent_a_get_method():
    from app.services.discovery_js import extract_js

    items = extract_js(
        "https://example.test/app.js",
        """
        fetch('/dynamic-method', {method: chosen});
        fetch('/spread', {...options});
        fetch('/computed', {[key]: value});
        fetch('/shorthand', {method});
    """,
    )
    assert len(items) == 4
    assert all(item["method"] is None for item in items)
