"""Run the fixed synthetic quality gate without repeating the full test suite."""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/quality/corpus-v1.json"


def summarize(xml_path, manifest, returncode):
    """Missing, skipped and errored cases cannot satisfy a required gate."""
    cases = []
    metrics = {}
    if xml_path.exists():
        for case in ET.parse(xml_path).iter("testcase"):
            node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
            status = "passed"
            for tag in ("skipped", "failure", "error"):
                if case.find(tag) is not None:
                    status = {"skipped": "skipped", "failure": "failed", "error": "error"}[tag]
            cases.append({"node": node, "status": status})
            if status == "passed":
                for prop in case.findall("properties/property"):
                    if prop.get("name") == "garden_metrics":
                        metrics.update(json.loads(prop.get("value", "{}")))
    dimensions = []
    for dimension in manifest["dimensions"]:
        checks = []
        for node in dimension["tests"]:
            matches = [c for c in cases if c["node"] == node or c["node"].startswith(node + "[")]
            checks.append(
                {
                    "node": node,
                    "status": "passed"
                    if matches and all(c["status"] == "passed" for c in matches)
                    else "blocked",
                    "results": matches,
                }
            )
        dimensions.append(
            {
                "name": dimension["name"],
                "checks": checks,
                "status": "passed"
                if checks and all(c["status"] == "passed" for c in checks)
                else "blocked",
            }
        )
    measured = {}
    for name, requirement in manifest["metrics"].items():
        value = metrics.get(name)
        valid = (
            isinstance(value, dict)
            and isinstance(value.get("numerator"), int)
            and isinstance(value.get("denominator"), int)
            and 0 <= value["numerator"] <= value["denominator"]
            and value["denominator"] > 0
        )
        ratio = value["numerator"] / value["denominator"] if valid else None
        passed = valid and (
            ratio >= requirement["min"] if "min" in requirement else ratio <= requirement["max"]
        )
        measured[name] = {
            "counts": value,
            "ratio": ratio,
            "status": "passed" if passed else "blocked",
            "definition": requirement["definition"],
        }
    passed = (
        returncode == 0
        and bool(dimensions)
        and all(d["status"] == "passed" for d in dimensions)
        and all(m["status"] == "passed" for m in measured.values())
        and all(c["status"] == "passed" for c in cases)
    )
    return {
        "status": "passed" if passed else "blocked",
        "pytest_exit_code": returncode,
        "executed": len(cases),
        "dimensions": dimensions,
        "metrics": measured,
        "nonpassing": [c for c in cases if c["status"] != "passed"],
    }


def markdown(report):
    lines = [
        "# Garden 合成质量基线",
        "",
        f"结果：**{report['status']}** · 语料：{report['corpus_version']} · 模式：{report['mode']}",
        f"提交：`{report['sha']}` · 工作区有改动：{report['dirty']} · Python：{report['python']}",
        f"环境：{report['platform']} · 耗时：{report['elapsed_seconds']} 秒",
        "",
        "这里只验证固定合成语料；不代表真实站点发现率，也不代替跨平台发布验收。",
        "",
        "| 质量维度 | 状态 |",
        "| --- | --- |",
    ]
    lines += [f"| {d['name']} | {d['status']} |" for d in report["dimensions"]]
    lines += ["", "## 固定语料指标", ""]
    for name, metric in report["metrics"].items():
        counts = metric["counts"]
        display = (
            f"{counts['numerator']}/{counts['denominator']}"
            if metric["ratio"] is not None
            else "未测量"
        )
        lines.append(f"- {name}: {display} · {metric['status']}。{metric['definition']}")
    lines += ["", "## 阻塞检查", ""]
    failures = {c["node"] for c in report["nonpassing"]}
    failures.update(
        c["node"] for d in report["dimensions"] for c in d["checks"] if c["status"] != "passed"
    )
    lines += [f"- `{node}`" for node in sorted(failures)] or ["无失败、跳过或缺失的必需用例。"]
    if report["pytest_exit_code"]:
        lines.append(f"pytest 退出码：{report['pytest_exit_code']}；请检查同目录 pytest.log。")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full", action="store_true", help="Run all tests once, then extract gates"
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".verification/quality")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    targets = (
        ["tests"]
        if args.full
        else sorted({node for d in manifest["dimensions"] for node in d["tests"]})
    )
    xml_path = output / "junit.xml"
    xml_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        "-o",
        "junit_family=legacy",
        f"--junitxml={xml_path}",
        *targets,
    ]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    started = time.monotonic()
    print(
        f"Running {'full suite' if args.full else 'quality gates'}; log: {output / 'pytest.log'}",
        flush=True,
    )
    with (output / "pytest.log").open("w") as log:
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
    report = {
        "schema_version": "quality-v1",
        "corpus_version": manifest["version"],
        "sha": sha,
        "dirty": dirty,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "full" if args.full else "focused",
        "command": command,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        **summarize(xml_path, manifest, result.returncode),
    }
    (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "quality.md").write_text(markdown(report))
    print(markdown(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
