from app.services.scan_result_presentation import format_finding_count


def test_finding_count_text():
    assert format_finding_count(104, 2) == "2 类（104 条原始观察）"


def test_empty_finding_count():
    assert format_finding_count(0, 0) == "0 类（0 条原始观察）"


def test_legacy_finding_count():
    assert format_finding_count(104, None) == "104 条原始观察（分类数未提供）"
