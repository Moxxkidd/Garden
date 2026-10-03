import json

import pytest

from app.core.errors import InputValidationError
from app.services.discovery_sources import parse_seed_input, parse_sitemap


def test_sitemap_namespaces_entities_normalization_and_provenance():
    items, indexes = parse_sitemap(
        "https://EXAMPLE.com:443/sitemap.xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://EXAMPLE.com:443/a?x=1&amp;y=2#top</loc></url></urlset>",
    )
    assert indexes == []
    assert items == [
        dict(
            url="https://example.com/a?x=1&y=2",
            asset_type="page",
            method="GET",
            source_kind="sitemap",
            source_url="https://example.com/sitemap.xml",
            auto_visit=True,
        )
    ]


def test_sitemap_index_only_direct_document_locations():
    items, indexes = parse_sitemap(
        "https://example.com/sitemap.xml",
        "<sitemapindex><sitemap><loc>https://other.test/sub.xml</loc></sitemap></sitemapindex>",
    )
    assert items == []
    assert indexes == ["https://other.test/sub.xml"]


@pytest.mark.parametrize(
    "body",
    [
        '<!DOCTYPE urlset [<!ENTITY x "secret">]><urlset/>',
        "<urlset>",
        "not XML",
        "<html/>",
        "<urlset>" + " " * (256 * 1024) + "</urlset>",
        "<urlset><url><loc>file:///secret</loc></url></urlset>",
        "<urlset><url><loc>https://u:secret@example.com/</loc></url></urlset>",
        "<urlset><url/></urlset>",
        "\x1f\x8bcompressed",
        '<urlset xmlns="urn:wrong"/>',
    ],
)
def test_sitemap_rejects_unsafe_or_invalid_documents_without_payload(body):
    with pytest.raises(InputValidationError) as error:
        parse_sitemap("https://example.com/sitemap.xml", body)
    assert "secret" not in str(error.value)


def test_sitemap_rejects_compressed_paths_and_overflow():
    with pytest.raises(InputValidationError):
        parse_sitemap("https://example.com/sitemap.xml.gz", "<urlset/>")
    with pytest.raises(InputValidationError):
        parse_sitemap(
            "https://example.com/sitemap.xml",
            "<urlset><url><loc>https://example.com/a</loc></url>"
            "<url><loc>https://example.com/b</loc></url></urlset>",
            limit=1,
        )


def test_url_import_preserves_line_numbers_and_duplicates():
    items = parse_seed_input("\ufeffhttps://EXAMPLE.com:443\n\nhttps://example.com/\n")
    assert [x["url"] for x in items] == ["https://example.com/"] * 2
    assert [x["source_index"] for x in items] == [1, 3]
    assert all(
        x["auto_visit"] and x["method"] == "GET" and x["source_kind"] == "url_import" for x in items
    )


@pytest.mark.parametrize(
    "text",
    [
        "/relative",
        "ftp://example.com",
        "https://u:secret@example.com",
        "https://example.com:99999/",
        "https://exa mple.com/",
        "https://example.com/\x00",
        "\ud800",
        "https://example.com/" + "a" * (1024 * 1024),
    ],
)
def test_url_import_rejects_invalid_urls_utf8_and_size(text):
    with pytest.raises(InputValidationError) as error:
        parse_seed_input(text)
    assert "secret" not in str(error.value)


def test_url_import_rejects_count_overflow():
    with pytest.raises(InputValidationError):
        parse_seed_input("https://a.test\nhttps://b.test", limit=1)


def api(**kwargs):
    return json.dumps(
        {
            "openapi": "3.1.0",
            "servers": [{"url": "https://example.com/api"}],
            "paths": {"/users/{id}": {"get": {}, "post": {}}},
            **kwargs,
        }
    )


def test_openapi_all_methods_are_declarations_and_paths_append_to_server():
    items = parse_seed_input(api(), format="openapi")
    assert [(x["url"], x["method"], x["auto_visit"]) for x in items] == [
        ("https://example.com/api/users/{id}", "GET", False),
        ("https://example.com/api/users/{id}", "POST", False),
    ]
    assert all(x["asset_type"] == "api" and x["source_kind"] == "openapi_import" for x in items)
    assert [x["source_index"] for x in items] == [1, 2]


def test_openapi_relative_and_override_servers():
    items = parse_seed_input(
        api(
            servers=[{"url": "/api"}],
            paths={
                "/root": {"get": {}},
                "/override": {
                    "servers": [{"url": "/v2"}],
                    "get": {},
                    "head": {"servers": [{"url": "https://other.test/v3"}]},
                },
            },
        ),
        format="openapi",
        base_url="https://example.com/spec/openapi.json",
    )
    assert [x["url"] for x in items] == [
        "https://example.com/api/root",
        "https://example.com/v2/override",
        "https://other.test/v3/override",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "openapi: 3.0.0",
        "{}",
        "[]",
        '{"swagger":"2.0"}',
        api(servers=[{"url": "https://{host}/"}]),
        api(paths={"/x": {"$ref": "https://secret.test/spec"}}),
        api(paths={"/x": {"get": [], "post": {}}}),
        api(paths={"relative": {"get": {}}}),
        api(servers=[{"url": "/api"}]),
        api(paths=[]),
        api(components={"schemas": {"X": {"$ref": "https://secret.test/x"}}}),
    ],
)
def test_openapi_rejects_unsupported_or_invalid_documents(text):
    with pytest.raises(InputValidationError) as error:
        parse_seed_input(text, format="openapi")
    assert "secret" not in str(error.value)


def test_openapi_internal_schema_ref_is_not_fetched():
    items = parse_seed_input(
        api(components={"schemas": {"X": {"$ref": "#/components/schemas/Y"}}}), format="openapi"
    )
    assert len(items) == 2


def test_openapi_default_server_requires_explicit_base_and_count_is_bounded():
    assert parse_seed_input(
        api(servers=[]), format="openapi", base_url="https://example.com/spec.json"
    )[0]["url"] == ("https://example.com/users/{id}")
    with pytest.raises(InputValidationError):
        parse_seed_input(api(), format="openapi", limit=1)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limit_rejected(limit):
    with pytest.raises(InputValidationError):
        parse_seed_input("", limit=limit)
    with pytest.raises(InputValidationError):
        parse_sitemap("https://example.com/map.xml", "<urlset/>", limit=limit)


def test_unknown_import_format_rejected():
    with pytest.raises(InputValidationError):
        parse_seed_input("", format="yaml")


@pytest.mark.parametrize("servers", [[{"url": "https://[broken/"}], [{"url": ""}]])
def test_bad_server_url_has_generic_validation_error(servers):
    with pytest.raises(InputValidationError):
        parse_seed_input(
            api(servers=servers), format="openapi", base_url="https://example.com/spec"
        )


@pytest.mark.parametrize("version", ["3.fake", "3.", "3.0", "3.1.0secret"])
def test_openapi_invalid_version_is_rejected(version):
    with pytest.raises(InputValidationError):
        parse_seed_input(api(openapi=version), format="openapi")


def test_local_path_refs_not_silently_resolved():
    with pytest.raises(InputValidationError):
        parse_seed_input(api(paths={"/x": {"$ref": "#/components/pathItems/X"}}), format="openapi")
