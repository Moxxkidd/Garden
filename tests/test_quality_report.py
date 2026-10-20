"""A green release gate requires executed checks and measured corpus counts."""

import json
from xml.etree.ElementTree import Element, ElementTree, SubElement

import pytest

from scripts.quality_baseline import summarize


@pytest.mark.parametrize(
    "condition", ["passed", "failure", "skipped", "missing", "empty_metric", "exit_error"]
)
def test_gate_never_promotes_missing_or_unsuccessful_evidence(tmp_path, condition):
    manifest = {
        "dimensions": [{"name": "boundary", "tests": ["tests/test_example.py::test_case"]}],
        "metrics": {"recall": {"min": 1, "definition": "fixed fixture only"}},
    }
    root = Element("testsuite")
    if condition != "missing":
        case = SubElement(root, "testcase", classname="tests.test_example", name="test_case")
        props = SubElement(case, "properties")
        SubElement(
            props,
            "property",
            name="garden_metrics",
            value=json.dumps(
                {
                    "recall": {
                        "numerator": 0 if condition == "empty_metric" else 1,
                        "denominator": 0 if condition == "empty_metric" else 1,
                    }
                }
            ),
        )
        if condition in {"failure", "skipped"}:
            SubElement(case, condition)
    xml_path = tmp_path / "junit.xml"
    ElementTree(root).write(xml_path)
    report = summarize(xml_path, manifest, 1 if condition == "exit_error" else 0)
    assert report["status"] == ("passed" if condition == "passed" else "blocked")
    if condition in {"failure", "skipped", "missing", "empty_metric"}:
        assert report["metrics"]["recall"]["ratio"] is None
