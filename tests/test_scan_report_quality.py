from datetime import datetime, timezone

from app.models.scan_run import ScanFinding
from app.services.scan_report_quality import extract_version_hints, project_finding_groups


def test_extract_version_hints_accepts_strong_contexts_with_provenance() -> None:
    hints = extract_version_hints(
        "https://example.test/vendor/jquery-ui-1.12.1.min.js?v=4.5.6",
        "/*! Swiper Version: 11.0.3 */\n/* @version 2.4.1 */",
        "script",
    )

    assert [(hint.value, hint.source, hint.detail) for hint in hints] == [
        ("1.12.1", "path", "jquery-ui"),
        ("4.5.6", "query", "v"),
        ("11.0.3", "body_marker", "swiper"),
        ("2.4.1", "body_marker", "version"),
    ]


def test_extract_version_hints_rejects_css_svg_dates_and_bare_numeric_paths() -> None:
    body = """
    path { d: 17.405 20.651 194.423; opacity: 0.18; }
    svg { viewBox: 0 0 952.311261 921.328619; }
    article-date: 2026-08-17;
    """

    assert (
        extract_version_hints(
            "https://example.test/2026.08.17/assets/style.css",
            body,
            "stylesheet",
        )
        == ()
    )
    assert (
        extract_version_hints(
            "https://example.test/app.js?v=123456.1.1",
            "@version 123456.1.1",
            "script",
        )
        == ()
    )


def test_extract_version_hints_is_bounded_deduplicated_and_capped() -> None:
    body = " ".join(
        ["@version 1.0.0", "@version 1.0.0"] + [f"@version 2.0.{index}" for index in range(10)]
    )
    hints = extract_version_hints("https://example.test/app.js", body, "script")

    assert [hint.value for hint in hints] == [
        "1.0.0",
        "2.0.0",
        "2.0.1",
        "2.0.2",
        "2.0.3",
    ]
    assert (
        extract_version_hints(
            "https://example.test/app.js",
            "x" * 4096 + " @version 9.9.9",
            "script",
        )
        == ()
    )


def _finding(
    identifier: int,
    *,
    title: str = "缺少 Content-Security-Policy 响应头",
    asset_ids: list[int] | None = None,
    evidence_ids: list[int] | None = None,
) -> ScanFinding:
    return ScanFinding(
        id=identifier,
        scan_run_id=1,
        dedup_key=f"finding-{identifier}",
        title=title,
        category="security-headers",
        severity="low",
        confidence="high",
        summary="HTML 响应未包含该安全头。",
        remediation="配置合适的响应头策略。",
        asset_ids=asset_ids or [],
        evidence_ids=evidence_ids or [],
        created_at=datetime.now(timezone.utc),
    )


def test_project_finding_groups_preserves_raw_rows_and_stable_order() -> None:
    findings = [
        _finding(3, asset_ids=[30], evidence_ids=[300]),
        _finding(1, asset_ids=[10, 11], evidence_ids=[100]),
        _finding(
            2,
            title="缺少 X-Content-Type-Options 响应头",
            asset_ids=[20],
            evidence_ids=[200],
        ),
    ]

    groups = project_finding_groups(findings)

    assert [group.representative.id for group in groups] == [1, 2]
    assert groups[0].observation_count == 2
    assert groups[0].asset_ids == (10, 11, 30)
    assert groups[0].evidence_ids == (100, 300)
    assert [finding.id for finding in findings] == [3, 1, 2]


def test_project_finding_groups_keeps_different_remediation_separate() -> None:
    first = _finding(1)
    second = _finding(2)
    second.remediation = "由边缘代理统一配置响应头。"

    assert len(project_finding_groups([first, second])) == 2
