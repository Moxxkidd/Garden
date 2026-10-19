"""Versioned synthetic corpus; metrics apply only to these explicit fixtures."""

import json
from urllib.parse import urlsplit

from app.db.bootstrap import session_scope
from app.schemas.assets import AssetQuery
from app.services.asset_catalog import AssetCatalogService
from tests.test_discovery_browser import collect, site

pytest_plugins = ["tests.helpers.asset_catalog"]


def test_fixed_browser_discovery_metrics(record_property):
    # This existing loopback fixture has delayed fetch and two hash routes.
    expected = {"/", "/delayed", "/view"}
    with site() as (base, requests):
        _, observations, candidates = collect(base + "/")
    observed = {urlsplit(response.final_url).path for response, _ in observations}
    eligible = {
        urlsplit(item["url"]).path
        for item in candidates
        if item["method"] == "GET"
        and item["auto_visit"]
        and urlsplit(item["url"]).netloc == urlsplit(base).netloc
    }
    metrics = {
        "discovery_recall": {"numerator": len(expected & observed), "denominator": len(expected)},
        "candidate_confirmation": {
            "numerator": len(eligible & observed),
            "denominator": len(eligible),
        },
    }
    record_property("garden_metrics", json.dumps(metrics))
    assert expected <= observed
    assert eligible and eligible <= observed
    assert not any(method == "POST" for method, _ in requests)


def test_fixed_grouping_metrics(asset_scan, record_property):
    from app.models.scan_run import ScanAsset

    # Explicit route families: origin, method, escaped path, parameter multiplicity.
    samples = [
        ("https://site.test/a?token=x", "GET", "same"),
        ("https://site.test/a?token=y", "GET", "same"),
        ("https://other.test/a?token=x", "GET", "origin"),
        ("https://site.test/a?token=x", "POST", "method"),
        ("https://site.test/a%2Fb?token=x", "GET", "escaped"),
        ("https://site.test/a/b?token=x", "GET", "path"),
        ("https://site.test/a?token=x&token=y", "GET", "multiplicity"),
    ]
    with session_scope() as session:
        original = session.get(ScanAsset, asset_scan[1])
        truth = {}
        for url, method, label in samples:
            row = ScanAsset(
                scan_run_id=original.scan_run_id,
                context_id=original.context_id,
                asset_type="page",
                method=method,
                url=url,
                discovered_at=original.discovered_at,
            )
            session.add(row)
            session.flush()
            truth[row.id] = label
        result = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=asset_scan[0], view="grouped")
        )
        predicted = {
            observation.record_id: group.asset_id
            for group in result.items
            for observation in group.observations
            if observation.record_id in truth
        }
    pairs = [(a, b) for a in truth for b in truth if a < b and truth[a] != truth[b]]
    false_merges = sum(predicted[a] == predicted[b] for a, b in pairs)
    record_property(
        "garden_metrics",
        json.dumps({"false_merge_rate": {"numerator": false_merges, "denominator": len(pairs)}}),
    )
    assert len(predicted) == len(truth)
    assert false_merges == 0
    same = [key for key, label in truth.items() if label == "same"]
    assert predicted[same[0]] == predicted[same[1]]
