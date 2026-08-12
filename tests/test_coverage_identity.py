"""资产身份和稳定响应指纹行为测试。"""

from __future__ import annotations

import pytest

from app.services.coverage_identity import (
    canonical_asset_identity,
    stable_response_signature,
)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://app/a?id=1&sort=name", "https://app/a?sort=date&id=9"),
        ("https://app:443/a/", "https://app/a"),
        ("http://APP:80/a#first", "http://app/a#second"),
        ("https://app/a?id=1&id=2", "https://app/a?id=9"),
    ],
)
def test_asset_identity_ignores_values_default_port_fragment_and_duplicate_names(
    left: str,
    right: str,
) -> None:
    assert canonical_asset_identity("GET", left) == canonical_asset_identity("GET", right)


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "https://app/a?id=1&sort=name"),
        ("GET", "https://app/b?id=1&sort=name"),
        ("GET", "https://app/a?id=1&filter=name"),
    ],
)
def test_asset_identity_distinguishes_method_path_and_parameter_names(
    method: str,
    url: str,
) -> None:
    baseline = canonical_asset_identity("GET", "https://app/a?id=1&sort=name")

    assert canonical_asset_identity(method, url) != baseline


def test_asset_identity_preserves_encoded_path_separators() -> None:
    encoded = canonical_asset_identity("GET", "https://app/reports%2F2026")
    separated = canonical_asset_identity("GET", "https://app/reports/2026")

    assert encoded != separated


def test_signature_ignores_timestamp_nonce_and_trace_id() -> None:
    first = "generated=2026-08-11T01:02:03Z nonce=abc trace_id=req-1 stable=users"
    second = "generated=2026-08-11T02:03:04Z nonce=xyz trace_id=req-2 stable=users"

    assert stable_response_signature(first, "text/plain") == stable_response_signature(
        second,
        "text/plain",
    )


def test_json_signature_ignores_dynamic_fields_and_object_order() -> None:
    first = '{"traceId":"req-1","users":["alice","bob"],"generatedAt":"2026-08-11T01:02:03Z"}'
    second = '{"generatedAt":"2026-08-11T02:03:04Z","users":["alice","bob"],"traceId":"req-2"}'

    assert stable_response_signature(first, "application/json") == stable_response_signature(
        second,
        "application/json; charset=utf-8",
    )


def test_signature_retains_stable_business_content() -> None:
    users = stable_response_signature('{"role":"user"}', "application/json")
    admins = stable_response_signature('{"role":"admin"}', "application/json")

    assert users != admins
