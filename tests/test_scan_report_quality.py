from app.services.scan_report_quality import extract_version_hints


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
