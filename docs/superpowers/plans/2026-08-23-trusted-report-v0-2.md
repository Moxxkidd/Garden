# Garden v0.2.0 可信报告版实施计划

> **For agentic workers / 面向智能体执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项执行本计划。所有步骤使用复选框跟踪；任何行为修改都必须遵循红—绿—重构。

**Goal / 目标：** 在不改变现有 CLI、Web、API、报告结构、扫描边界和状态语义的前提下，为版本线索增加可追溯来源、把发现分组抽成独立质量投影，并发布 Garden v0.2.0。

**Architecture / 架构：** 新增纯函数模块 `app/services/scan_report_quality.py`，统一负责可信版本线索和发现分组投影。采集流水线只负责把提取结果保存到现有 JSON，报告服务只消费质量投影；原始资产、证据和发现继续完整持久化，旧记录采用保守回退规则，不增加数据库迁移。

**Tech Stack / 技术栈：** Python 3.10+、FastAPI、Pydantic v2、SQLAlchemy 2、Typer、httpx、pytest、Ruff、Hatchling。

**Spec / 规格：** `docs/superpowers/specs/2026-08-23-trusted-report-v0-2-design.md`

## 全局约束

- 兼容性基线固定为 `origin/main` 的 `af0d2d4`；除版本号外，不改变现有用户体验。
- 保持 CLI 命令、别名、参数、默认值和退出语义不变。
- 保持 Web 路由、首页 URL 表单和扫描详情导航不变。
- 保持现有 API 路由、请求字段、响应字段名称与类型不变；仅允许在现有可扩展证据 JSON 中增加可选元数据。
- 保持报告章节名称、顺序、资产/证据引用、覆盖告警与请求失败分区不变。
- 保持被动 GET、同源限制、地址策略复核、预算、重试和超时语义不变。
- 不修改 ORM 模型、Alembic 迁移、模板、API 路由、CLI 参数声明或网络策略。
- 不增加第三方依赖；支持版本保持 Python `>=3.10`。
- 正文版本分析仍限制在前 4096 个字符；版本线索去重后最多五个。
- 测试不得访问南京农业大学线上站点，也不得执行外部 DNS 或公网请求。
- 每个行为任务必须先看到预期失败，再写最小实现；表征测试用于冻结当前行为，预期在修改生产代码前通过。

---

## 文件职责映射

- 新建 `app/services/scan_report_quality.py`：版本候选、来源验证、旧记录回退和发现分组的唯一质量策略入口。
- 修改 `app/services/scan_pipeline.py`：调用质量模块并把字符串列表及可选来源保存到现有证据 JSON。
- 修改 `app/services/scan_reporting.py`：消费类型化分组和可信报告版本值，不再内置领域分类逻辑。
- 新建 `tests/test_scan_report_quality.py`：纯函数单元测试，不依赖数据库或网络。
- 修改 `tests/test_url_scan_pipeline.py`：流水线、旧记录和南京农业大学模式的本地集成回归。
- 新建 `tests/test_v020_compatibility.py`：冻结 CLI、Web、API、报告结构和状态语义。
- 修改 `pyproject.toml`、`app/core/settings.py`：唯一正式版本号来源改为 `0.2.0`。
- 修改 `tests/test_cli.py`、`tests/test_install_script.py`：发布版本和正式安装入口断言。
- 修改 `README.md`，新建 `docs/development-log/2026-08-23-v0-2-trusted-report.md`：发布说明和可信度边界。

---

### 任务 1：冻结现有用户体验契约

**Files / 文件：**

- Create / 新建：`tests/test_v020_compatibility.py`
- Test / 复用：`tests/test_api.py`
- Test / 复用：`tests/test_quick_scan_cli.py`
- Test / 复用：`tests/test_scan_cancellation.py`
- Test / 复用：`tests/test_url_scan_pipeline.py`
- Test / 复用：`tests/test_install_script.py`

**Interfaces / 接口：**

- Consumes / 使用：`app.cli.main.app`、`ScanOptions`、`ScanStartRequest`、`ScanRunView`、`ScanRunStatus`、`TERMINAL_SCAN_RUN_STATUSES`、`ScanReportService._render()`。
- Produces / 产出：后续任务必须持续通过的用户体验表征测试；不产出生产接口。

- [ ] **Step 1：编写当前主线应直接通过的表征测试**

创建 `tests/test_v020_compatibility.py`：

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES
from app.schemas.scan import ScanOptions, ScanRunStatus, ScanRunView, ScanStartRequest
from app.services.scan_reporting import ScanReportService


runner = CliRunner()


def test_cli_scan_flags_and_defaults_are_unchanged() -> None:
    result = runner.invoke(cli_app, ["scan", "--help"])

    assert result.exit_code == 0
    for flag in (
        "--url",
        "--max-pages",
        "--max-resources",
        "--max-depth",
        "--request-timeout",
        "--overall-timeout",
        "--retries",
        "--detach",
        "--ui-port",
    ):
        assert flag in result.output
    assert ScanOptions().model_dump() == {
        "max_pages": 50,
        "max_resources": 200,
        "max_depth": 2,
        "request_timeout_seconds": None,
        "overall_timeout_seconds": None,
        "retry_attempts": None,
        "max_redirects": 5,
        "user_agent": "Garden-Authorized-Asset-Scanner/0.2",
    }


def test_scan_api_schema_fields_are_unchanged() -> None:
    assert set(ScanStartRequest.model_fields) == {"url", "options"}
    assert set(ScanRunView.model_fields) == {
        "id",
        "mode",
        "target_id",
        "source_run_id",
        "input_url",
        "normalized_url",
        "status",
        "current_stage",
        "progress",
        "retry_count",
        "report_path",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
        "report_generated_at",
        "created_at",
        "contexts",
        "stages",
        "failures",
        "asset_count",
        "evidence_count",
        "finding_count",
    }


def test_scan_status_semantics_are_unchanged() -> None:
    assert {status.value for status in ScanRunStatus} == {
        "queued",
        "running",
        "completed",
        "completed_with_warnings",
        "failed",
        "incomplete",
        "interrupted",
    }
    assert TERMINAL_SCAN_RUN_STATUSES == {
        "completed",
        "completed_with_warnings",
        "failed",
        "incomplete",
        "interrupted",
    }


def test_homepage_keeps_single_url_submission(app) -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="scan-url"' in response.text
    assert 'name="url"' in response.text
    assert 'type="url"' in response.text
    assert "Start scan" in response.text


def test_report_section_names_and_order_are_unchanged(tmp_path) -> None:
    run = SimpleNamespace(
        id=7,
        normalized_url="http://127.0.0.1/",
        status="completed",
        options={},
        failures=[],
        assets=[],
        evidence=[],
        findings=[],
        stages=[],
    )
    report = "\n".join(
        ScanReportService(tmp_path)._render(run, datetime(2026, 8, 23, tzinfo=timezone.utc))
    )
    headings = [
        "执行摘要",
        "扫描范围",
        "发现的资产",
        "关键属性",
        "证据",
        "已观察到的安全控制",
        "风险或关注项",
        "扫描覆盖范围",
        "失败或未覆盖部分",
        "覆盖告警",
        "请求失败",
        "生成时间",
    ]

    positions = [report.index(f"## {heading}") for heading in headings]
    assert positions == sorted(positions)
```

- [ ] **Step 2：运行表征测试，确认当前主线行为已被正确记录**

Run / 运行：

```bash
python -m pytest -q tests/test_v020_compatibility.py tests/test_api.py tests/test_quick_scan_cli.py tests/test_scan_cancellation.py
python -m pytest -q \
  tests/test_url_scan_pipeline.py::test_collection_blocks_cross_origin_redirect_before_following_it \
  tests/test_url_scan_pipeline.py::test_report_uses_request_buckets_and_aggregates_repeated_findings \
  tests/test_install_script.py::test_installed_launchers_survive_runtime_move \
  tests/test_install_script.py::test_installed_launcher_does_not_use_module_mode_from_repository_cwd
```

Expected / 预期：全部通过。第一条命令冻结 CLI、Web、API、报告章节和中断语义；第二条命令冻结覆盖告警分区、原始数量与聚合数量、`gardenctl` 兼容入口以及仓库目录启动行为。若新增表征测试失败，先校正测试对 `af0d2d4` 的描述，不得为了让表征测试通过而修改生产行为。

- [ ] **Step 3：运行静态检查**

Run / 运行：

```bash
python -m ruff check tests/test_v020_compatibility.py
python -m ruff format --check tests/test_v020_compatibility.py
```

Expected / 预期：两条命令均退出 0。

- [ ] **Step 4：提交兼容性基线**

```bash
git add tests/test_v020_compatibility.py
git commit -m "测试 v0.2.0 用户体验兼容基线"
```

---

### 任务 2：用纯函数提取带来源的可信版本线索

**Files / 文件：**

- Create / 新建：`app/services/scan_report_quality.py`
- Create / 新建：`tests/test_scan_report_quality.py`

**Interfaces / 接口：**

- Produces / 产出：
  - `VersionHint(value: str, source: Literal["query", "path", "body_marker"], detail: str)`
  - `extract_version_hints(url: str, body: str, asset_type: str) -> tuple[VersionHint, ...]`
- Consumes / 使用：只使用标准库；不依赖数据库、网络、渲染器或全局配置。

- [ ] **Step 1：编写接受可信上下文的失败测试**

创建 `tests/test_scan_report_quality.py`：

```python
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
```

- [ ] **Step 2：编写拒绝南京农业大学数字噪声的失败测试**

继续添加：

```python
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
```

- [ ] **Step 3：运行测试并确认红灯原因是质量模块尚不存在**

Run / 运行：

```bash
python -m pytest -q tests/test_scan_report_quality.py
```

Expected / 预期：收集阶段失败，错误包含 `ModuleNotFoundError: No module named 'app.services.scan_report_quality'`。

- [ ] **Step 4：实现最小可信版本提取模块**

新建 `app/services/scan_report_quality.py`：

```python
"""Pure report-quality policy for trusted metadata and finding projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse


_VERSION_PART = r"\d{1,5}"
_VERSION_CORE = rf"{_VERSION_PART}\.{_VERSION_PART}(?:\.{_VERSION_PART})?"
_VERSION = rf"(?<!\d){_VERSION_CORE}(?!\d)"
_VERSION_FULL = re.compile(rf"^{_VERSION}$")
_PATH_VERSION = re.compile(
    rf"(?i)(?P<label>[a-z][a-z0-9]*(?:[._-][a-z][a-z0-9]*)*)"
    rf"[-@_](?P<value>{_VERSION})(?=$|[./_-])"
)
_BODY_VERSION = re.compile(
    rf"(?i)(?:@version|\bversion\b|\bver\b)\s*[:=_-]?\s*v?"
    rf"(?P<value>{_VERSION})"
)
_BODY_COMPONENT_VERSION = re.compile(
    rf"(?i)\b(?P<label>jquery|swiper|slick|jwplayer|pdf\.js)\b\s*"
    rf"(?:version|ver|v)?\s*[:=_-]?\s*(?P<value>{_VERSION})"
)
_VERSION_QUERY_KEYS = ("v", "ver", "version")
_BODY_PREFIX_LIMIT = 4096
_MAX_HINTS = 5


@dataclass(frozen=True)
class VersionHint:
    value: str
    source: Literal["query", "path", "body_marker"]
    detail: str


def extract_version_hints(url: str, body: str, asset_type: str) -> tuple[VersionHint, ...]:
    parsed = urlparse(url)
    candidates: list[VersionHint] = []

    for match in _PATH_VERSION.finditer(parsed.path):
        candidates.append(VersionHint(match.group("value"), "path", match.group("label").lower()))

    query = parse_qs(parsed.query)
    for key in _VERSION_QUERY_KEYS:
        for raw_value in query.get(key, []):
            value = raw_value.strip()
            if _VERSION_FULL.fullmatch(value):
                candidates.append(VersionHint(value, "query", key))

    if asset_type in {"stylesheet", "script"}:
        prefix = body[:_BODY_PREFIX_LIMIT]
        for match in _BODY_COMPONENT_VERSION.finditer(prefix):
            candidates.append(
                VersionHint(
                    match.group("value"),
                    "body_marker",
                    match.group("label").lower(),
                )
            )
        for match in _BODY_VERSION.finditer(prefix):
            candidates.append(VersionHint(match.group("value"), "body_marker", "version"))

    unique: list[VersionHint] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.value in seen:
            continue
        seen.add(candidate.value)
        unique.append(candidate)
        if len(unique) == _MAX_HINTS:
            break
    return tuple(unique)
```

- [ ] **Step 5：运行单元测试，确认绿灯**

Run / 运行：

```bash
python -m pytest -q tests/test_scan_report_quality.py
python -m ruff check app/services/scan_report_quality.py tests/test_scan_report_quality.py
python -m ruff format --check app/services/scan_report_quality.py tests/test_scan_report_quality.py
```

Expected / 预期：全部退出 0。若两个正文正则对同一个版本产生重复候选，最终结果仍只保留首次出现的来源。

- [ ] **Step 6：提交纯版本提取器**

```bash
git add app/services/scan_report_quality.py tests/test_scan_report_quality.py
git commit -m "实现可信版本线索提取"
```

---

### 任务 3：把版本来源接入流水线并建立南京农业大学回归

**Files / 文件：**

- Modify / 修改：`app/services/scan_pipeline.py:5-9, 635, 650, 669-677, 725-743`
- Modify / 修改：`tests/test_url_scan_pipeline.py:11-14, 572-595`

**Interfaces / 接口：**

- Consumes / 使用：任务 2 的 `extract_version_hints()` 和 `VersionHint`。
- Produces / 产出：
  - 现有 `resource_summary.version_hints: list[str]`；
  - 新增可选 `resource_summary.version_hint_details: list[dict[str, str]]`，每项固定包含 `value`、`source`、`detail`。

- [ ] **Step 1：先扩展现有流水线测试，要求保存版本来源**

把 `ScanEvidence` 加入 `tests/test_url_scan_pipeline.py` 的模型导入，并在 `test_version_hints_require_context_and_include_version_query` 末尾添加：

```python
    with session_scope() as session:
        evidence = session.scalar(
            select(ScanEvidence).where(
                ScanEvidence.scan_run_id == scan.id,
                ScanEvidence.source_url.like("%/style.css?v=1.0.0"),
            )
        )
        resource_summary = evidence.data["resource_summary"]

    assert resource_summary["version_hints"] == ["1.0.0"]
    assert resource_summary["version_hint_details"] == [
        {"value": "1.0.0", "source": "query", "detail": "v"}
    ]
```

- [ ] **Step 2：增加完整的本地南京农业大学模式回归测试**

继续在 `tests/test_url_scan_pipeline.py` 增加：

```python
def test_njau_style_report_keeps_104_raw_findings_and_trusted_versions(tmp_path) -> None:
    # 根页面加前 51 个子页面会形成 52 个已采集 HTML 资产；最后一个候选留在预算外。
    page_links = "".join(f'<a href="/page-{index}">Page</a>' for index in range(52))

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    page_links
                    + '<link rel="stylesheet" href="/style.css?v=1.0.0">'
                    + '<script src="/swiper-11.0.3.js"></script>'
                    + '<img src="/map.svg">'
                ),
            )
        if request.url.path.startswith("/page-"):
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><title>NJAU fixture page</title></html>",
            )
        if request.url.path == "/style.css":
            return httpx.Response(
                200,
                headers={"content-type": "text/css"},
                text=("/* @version 2.4.1 */ path { d: 17.405 20.651 194.423; opacity: 0.18; }"),
            )
        if request.url.path == "/swiper-11.0.3.js":
            return httpx.Response(
                200,
                headers={"content-type": "application/javascript"},
                text="/*! Swiper Version: 11.0.3 */",
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/svg+xml"},
            text='<svg viewBox="0 0 952.311261 921.328619"></svg>',
        )

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(
            max_pages=52,
            max_resources=3,
            max_depth=1,
            retry_attempts=0,
        ),
    )
    report = service.read_report(scan.id)

    assert scan.finding_count == 104
    assert scan.status == "completed_with_warnings"
    assert "2 类（104 条原始观察）" in report
    assert "- 覆盖告警：1" in report
    assert "- 请求或阶段失败：0" in report
    assert "版本线索=1.0.0, 2.4.1" in report
    assert "版本线索=11.0.3" in report
    for false_version in ("17.405", "20.651", "194.423", "0.18", "952.311261"):
        assert false_version not in report

    with session_scope() as session:
        resource_evidence = session.scalars(
            select(ScanEvidence).where(ScanEvidence.scan_run_id == scan.id)
        ).all()
    details = {
        item.source_url: item.data["resource_summary"]["version_hint_details"]
        for item in resource_evidence
        if item.data.get("resource_summary")
    }
    assert details["http://127.0.0.1/style.css?v=1.0.0"] == [
        {"value": "1.0.0", "source": "query", "detail": "v"},
        {"value": "2.4.1", "source": "body_marker", "detail": "version"},
    ]
    assert details["http://127.0.0.1/swiper-11.0.3.js"] == [
        {"value": "11.0.3", "source": "path", "detail": "swiper"}
    ]
    coverage = report.split("## 覆盖告警", 1)[1].split("## 请求失败", 1)[0]
    request_failures = report.split("## 请求失败", 1)[1]
    assert "coverage_limit_reached" in coverage
    assert "coverage_limit_reached" not in request_failures
```

- [ ] **Step 3：运行新增测试并确认红灯**

Run / 运行：

```bash
python -m pytest -q \
  tests/test_url_scan_pipeline.py::test_version_hints_require_context_and_include_version_query \
  tests/test_url_scan_pipeline.py::test_njau_style_report_keeps_104_raw_findings_and_trusted_versions
```

Expected / 预期：失败于缺少 `version_hint_details`；不得出现真实网络连接失败。

- [ ] **Step 4：用质量提取器替换流水线私有正则**

在 `app/services/scan_pipeline.py`：

```python
from app.services.scan_report_quality import extract_version_hints
```

在 `_persist_fetch()` 中替换原调用：

```python
        version_hint_candidates = extract_version_hints(
            result.final_url,
            result.body_text,
            asset_type,
        )
        version_hints = [candidate.value for candidate in version_hint_candidates]
        version_hint_details = [
            {
                "value": candidate.value,
                "source": candidate.source,
                "detail": candidate.detail,
            }
            for candidate in version_hint_candidates
        ]
```

在静态资源 `resource_summary` 中增加：

```python
                "version_hint_details": version_hint_details,
```

删除 `_version_hints()`；删除只被该方法使用的 `re` 和 `parse_qs` 导入，保留文件后部仍然使用的 `urlparse`。

- [ ] **Step 5：运行聚焦回归并确认绿灯**

Run / 运行：

```bash
python -m pytest -q \
  tests/test_scan_report_quality.py \
  tests/test_url_scan_pipeline.py::test_version_hints_require_context_and_include_version_query \
  tests/test_url_scan_pipeline.py::test_njau_style_report_keeps_104_raw_findings_and_trusted_versions \
  tests/test_url_scan_pipeline.py::test_report_uses_request_buckets_and_aggregates_repeated_findings
python -m ruff check app/services/scan_pipeline.py tests/test_url_scan_pipeline.py
```

Expected / 预期：全部退出 0；南京农业大学夹具产生 104 条原始发现、报告显示两类问题，且不存在外部网络请求。

- [ ] **Step 6：提交流水线集成**

```bash
git add app/services/scan_pipeline.py tests/test_url_scan_pipeline.py
git commit -m "记录可信版本线索来源"
```

---

### 任务 4：把发现分组抽成类型化质量投影

**Files / 文件：**

- Modify / 修改：`app/services/scan_report_quality.py`
- Modify / 修改：`app/services/scan_reporting.py:14-16, 63-67, 173-202, 276-288`
- Modify / 修改：`tests/test_scan_report_quality.py`
- Test / 复用：`tests/test_url_scan_pipeline.py:597-622`

**Interfaces / 接口：**

- Produces / 产出：
  - `FindingGroup(representative: ScanFinding, observation_count: int, asset_ids: tuple[int, ...], evidence_ids: tuple[int, ...])`
  - `project_finding_groups(findings: Sequence[ScanFinding]) -> tuple[FindingGroup, ...]`
- Consumes / 使用：持久化的原始 `ScanFinding`；不修改或合并数据库记录。

- [ ] **Step 1：编写发现分组的失败测试**

在 `tests/test_scan_report_quality.py` 增加：

```python
from datetime import datetime, timezone

from app.models.scan_run import ScanFinding
from app.services.scan_report_quality import project_finding_groups


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
```

- [ ] **Step 2：运行测试并确认红灯**

Run / 运行：

```bash
python -m pytest -q tests/test_scan_report_quality.py -k project_finding_groups
```

Expected / 预期：导入失败，提示缺少 `project_finding_groups`。

- [ ] **Step 3：实现不可变分组投影**

在 `app/services/scan_report_quality.py` 中增加：

```python
from collections.abc import Sequence

from app.models.scan_run import ScanFinding


@dataclass(frozen=True)
class FindingGroup:
    representative: ScanFinding
    observation_count: int
    asset_ids: tuple[int, ...]
    evidence_ids: tuple[int, ...]


def project_finding_groups(findings: Sequence[ScanFinding]) -> tuple[FindingGroup, ...]:
    grouped: dict[tuple[str, ...], list[ScanFinding]] = {}
    for finding in sorted(findings, key=lambda item: item.id):
        key = (
            finding.title,
            finding.category,
            finding.severity,
            finding.confidence,
            finding.summary,
            finding.remediation,
        )
        grouped.setdefault(key, []).append(finding)

    return tuple(
        FindingGroup(
            representative=items[0],
            observation_count=len(items),
            asset_ids=tuple(sorted({value for item in items for value in item.asset_ids})),
            evidence_ids=tuple(sorted({value for item in items for value in item.evidence_ids})),
        )
        for items in grouped.values()
    )
```

- [ ] **Step 4：让报告服务消费类型化分组，保持文字输出不变**

在 `app/services/scan_reporting.py` 导入：

```python
from app.services.scan_report_quality import project_finding_groups
```

把 `self._group_findings(findings)` 改为 `project_finding_groups(findings)`；把发现渲染循环改为：

```python
for group in finding_groups:
    first = group.representative
    evidence_refs = self._sample_refs("E", list(group.evidence_ids))
    asset_refs = self._sample_refs("A", list(group.asset_ids))
    heading_id = f"F{first.id} 等" if group.observation_count > 1 else f"F{first.id}"
    lines.extend([
        f"### {heading_id}：{first.title}",
        "",
        f"- 严重性 / 置信度：{first.severity} / {first.confidence}",
        f"- 类别：{first.category}",
        (f"- 影响范围：{len(group.asset_ids)} 个资产，{len(group.evidence_ids)} 条证据"),
        f"- 关联资产样本：{asset_refs}",
        f"- 关联证据样本：{evidence_refs}",
        f"- 说明：{first.summary}",
        f"- 建议：{first.remediation}",
        "",
    ])
```

删除 `ScanReportService._group_findings()`，不得修改报告章节、标题或摘要格式。

- [ ] **Step 5：运行单元和报告回归**

Run / 运行：

```bash
python -m pytest -q \
  tests/test_scan_report_quality.py \
  tests/test_url_scan_pipeline.py::test_report_uses_request_buckets_and_aggregates_repeated_findings \
  tests/test_url_scan_pipeline.py::test_njau_style_report_keeps_104_raw_findings_and_trusted_versions
python -m ruff check app/services/scan_report_quality.py app/services/scan_reporting.py
```

Expected / 预期：全部通过；数据库中的原始发现数量仍为 104，报告仍为两类。

- [ ] **Step 6：提交发现投影**

```bash
git add app/services/scan_report_quality.py app/services/scan_reporting.py tests/test_scan_report_quality.py
git commit -m "抽取报告发现分组投影"
```

---

### 任务 5：保守处理缺少来源的旧版版本线索

**Files / 文件：**

- Modify / 修改：`app/services/scan_report_quality.py`
- Modify / 修改：`app/services/scan_reporting.py:139-145`
- Modify / 修改：`tests/test_scan_report_quality.py`
- Modify / 修改：`tests/test_url_scan_pipeline.py`

**Interfaces / 接口：**

- Produces / 产出：`report_version_values(source_url: str, resource_summary: Mapping[str, object]) -> list[str]`。
- Consumes / 使用：新记录中的 `version_hint_details`；旧记录中的 `version_hints` 和证据 `source_url`。

- [ ] **Step 1：编写新旧记录选择规则的失败测试**

在 `tests/test_scan_report_quality.py` 增加：

```python
from app.services.scan_report_quality import report_version_values


def test_report_version_values_uses_valid_new_provenance() -> None:
    summary = {
        "version_hints": ["6.8", "17.405"],
        "version_hint_details": [
            {"value": "6.8", "source": "body_marker", "detail": "jwplayer"},
            {"value": "bad", "source": "body_marker", "detail": "version"},
        ],
    }

    assert report_version_values("https://example.test/player.js", summary) == ["6.8"]


def test_report_version_values_revalidates_legacy_hints_from_url_only() -> None:
    summary = {"version_hints": ["1.12.1", "17.405", "0.18"]}

    assert report_version_values(
        "https://example.test/jquery-ui-1.12.1/jquery-ui.css",
        summary,
    ) == ["1.12.1"]


def test_report_version_values_ignores_malformed_optional_metadata() -> None:
    assert (
        report_version_values(
            "https://example.test/app.js",
            {"version_hints": ["9.9.9"], "version_hint_details": "malformed"},
        )
        == []
    )
    assert (
        report_version_values(
            "https://example.test/app.js",
            {
                "version_hint_details": [
                    {"value": "1.2.3", "source": [], "detail": "version"},
                    {"value": "17.405", "source": "path", "detail": "not-in-url"},
                ]
            },
        )
        == []
    )
```

- [ ] **Step 2：运行测试并确认红灯**

Run / 运行：

```bash
python -m pytest -q tests/test_scan_report_quality.py -k report_version_values
```

Expected / 预期：导入失败，提示缺少 `report_version_values`。

- [ ] **Step 3：实现报告版本值选择器**

在 `app/services/scan_report_quality.py` 增加，并把任务 4 的集合导入扩展为
`from collections.abc import Mapping, Sequence`：

```python
_TRUSTED_SOURCES = {"query", "path", "body_marker"}
_TRUSTED_BODY_DETAILS = {
    "version",
    "jquery",
    "swiper",
    "slick",
    "jwplayer",
    "pdf.js",
}


def report_version_values(
    source_url: str,
    resource_summary: Mapping[str, object],
) -> list[str]:
    details = resource_summary.get("version_hint_details")
    if details is not None:
        if not isinstance(details, list):
            return []
        values: list[str] = []
        url_provenance = {
            (hint.value, hint.source, hint.detail)
            for hint in extract_version_hints(source_url, "", "document")
        }
        for item in details:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            source = item.get("source")
            detail = item.get("detail")
            if (
                isinstance(value, str)
                and _VERSION_FULL.fullmatch(value)
                and isinstance(source, str)
                and source in _TRUSTED_SOURCES
                and isinstance(detail, str)
                and bool(detail)
                and (
                    (source == "body_marker" and detail in _TRUSTED_BODY_DETAILS)
                    or (value, source, detail) in url_provenance
                )
                and value not in values
            ):
                values.append(value)
            if len(values) == _MAX_HINTS:
                break
        return values

    raw_values = resource_summary.get("version_hints")
    if not isinstance(raw_values, list):
        return []
    url_values = {hint.value for hint in extract_version_hints(source_url, "", "document")}
    values: list[str] = []
    for value in raw_values:
        if isinstance(value, str) and value in url_values and value not in values:
            values.append(value)
        if len(values) == _MAX_HINTS:
            break
    return values
```

- [ ] **Step 4：运行选择器单元测试，确认绿灯**

Run / 运行：

```bash
python -m pytest -q tests/test_scan_report_quality.py -k report_version_values
```

Expected / 预期：全部通过；格式错误或无法由 URL 验证的来源元数据被安全忽略。

- [ ] **Step 5：编写报告集成的旧记录失败测试**

在 `tests/test_url_scan_pipeline.py` 增加：

```python
def test_regenerated_legacy_report_omits_unverifiable_body_versions(tmp_path) -> None:
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<link rel="stylesheet" href="/jquery-ui-1.12.1/jquery-ui.css">',
            )
        return httpx.Response(200, headers={"content-type": "text/css"}, text="body {}")

    service = _service(tmp_path, handler)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=1, max_resources=1, retry_attempts=0),
    )

    with session_scope() as session:
        evidence = session.scalar(
            select(ScanEvidence).where(
                ScanEvidence.scan_run_id == scan.id,
                ScanEvidence.source_url.contains("jquery-ui"),
            )
        )
        original_data = dict(evidence.data)
        legacy_summary = dict(original_data["resource_summary"])
        legacy_summary.pop("version_hint_details")
        legacy_summary["version_hints"] = ["1.12.1", "17.405", "0.18"]
        evidence.data = {**original_data, "resource_summary": legacy_summary}
        session.flush()
        before_render = dict(evidence.data)
        path = ScanReportService(tmp_path / "legacy-reports").generate(session, scan.id)
        report = Path(path).read_text(encoding="utf-8")
        assert evidence.data == before_render

    assert "版本线索=1.12.1" in report
    assert "17.405" not in report
    assert "0.18" not in report
```

同时在测试文件顶部导入 `Path` 和 `ScanEvidence`。

- [ ] **Step 6：先运行集成测试并确认当前报告仍展示错误旧值**

Run / 运行：

```bash
python -m pytest -q \
  tests/test_url_scan_pipeline.py::test_regenerated_legacy_report_omits_unverifiable_body_versions
```

Expected / 预期：失败，报告中仍包含 `17.405` 或 `0.18`。

- [ ] **Step 7：让报告服务统一使用可信选择器**

在 `app/services/scan_reporting.py` 导入 `report_version_values`，把静态资源索引中的版本值构造替换为：

```python
versions = ", ".join(report_version_values(item.source_url, resource_summary)) or "未识别"
```

- [ ] **Step 8：运行旧记录、新记录和报告结构回归**

Run / 运行：

```bash
python -m pytest -q \
  tests/test_scan_report_quality.py \
  tests/test_url_scan_pipeline.py::test_regenerated_legacy_report_omits_unverifiable_body_versions \
  tests/test_url_scan_pipeline.py::test_njau_style_report_keeps_104_raw_findings_and_trusted_versions \
  tests/test_v020_compatibility.py::test_report_section_names_and_order_are_unchanged
python -m ruff check app/services/scan_report_quality.py app/services/scan_reporting.py
```

Expected / 预期：全部通过；重新生成报告不修改旧证据 JSON。

- [ ] **Step 9：提交旧记录兼容策略**

```bash
git add \
  app/services/scan_report_quality.py \
  app/services/scan_reporting.py \
  tests/test_scan_report_quality.py \
  tests/test_url_scan_pipeline.py
git commit -m "保守过滤旧版报告版本线索"
```

---

### 任务 6：发布 Garden v0.2.0 并执行完整门禁

**Files / 文件：**

- Modify / 修改：`pyproject.toml:7`
- Modify / 修改：`app/core/settings.py:24`
- Modify / 修改：`tests/test_cli.py:29-32`
- Modify / 修改：`tests/test_install_script.py:34-96, 144-179, 336-363`
- Modify / 修改：`README.md` 的报告说明
- Create / 新建：`docs/development-log/2026-08-23-v0-2-trusted-report.md`
- Verify / 验证：`tests/test_migrations.py:280-340`
- Verify / 验证：`scripts/e2e_url_scan.py`

**Interfaces / 接口：**

- Consumes / 使用：任务 1—5 的兼容性和质量门禁。
- Produces / 产出：包版本、应用默认版本和正式 CLI 输出统一为 `0.2.0`；不改变命令调用方式。

- [ ] **Step 1：先把版本断言改为 0.2.0**

在 `tests/test_cli.py` 中修改：

```python
def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Garden 0.2.0" in result.stdout
```

把 `tests/test_install_script.py` 中假 Python 输出和三个正式启动断言里的 `Garden 0.1.0` 全部改为 `Garden 0.2.0`。

- [ ] **Step 2：运行版本测试并确认红灯**

Run / 运行：

```bash
python -m pytest -q \
  tests/test_cli.py::test_version_command \
  tests/test_install_script.py::test_installed_launchers_survive_runtime_move \
  tests/test_install_script.py::test_installed_launcher_does_not_use_module_mode_from_repository_cwd
```

Expected / 预期：至少 `test_version_command` 失败，实际值仍为 `Garden 0.1.0`。

- [ ] **Step 3：修改唯一正式版本元数据**

在 `pyproject.toml`：

```toml
version = "0.2.0"
```

在 `app/core/settings.py`：

```python
project_version: str = Field(default="0.2.0", alias="GARDEN_PROJECT_VERSION")
```

不要修改 `ScanOptions.user_agent` 的现有 `Garden-Authorized-Asset-Scanner/0.2`，也不要改写历史文档中的旧 wheel 文件名或历史扫描示例。

- [ ] **Step 4：增加中文发布日志并更新 README 的报告说明**

新建 `docs/development-log/2026-08-23-v0-2-trusted-report.md`，必须包含以下完整结构：

```markdown
# Garden v0.2.0 可信报告版开发日志

日期：2026-08-23

## 用户体验兼容

- CLI、Web、HTTP API、报告章节、扫描边界和状态语义保持不变。
- 原始资产、证据和发现继续完整保存；不增加数据库迁移。

## 报告可信度

- 版本线索只接受查询参数、具名组件路径或显式正文标记等可信上下文。
- CSS 数值、SVG 坐标、日期和无关小数不再被展示为版本。
- 重复发现以问题类型聚合展示，同时保留原始观察数量和引用样本。
- 缺少来源信息的旧记录只展示能够从 URL 重新验证的版本线索。

## 安装与发布

- 包版本和应用默认版本统一为 0.2.0。
- 沿用主线已有的 Python 3.12 发现、旧公网策略迁移和仓库源码遮蔽防护。
```

把 README “报告内容”中的静态资源说明改为：

```markdown
- 来源明确且默认脱敏的证据；静态资源使用大小、SHA-256、带可信上下文的版本线索和安全信号摘要
```

- [ ] **Step 5：运行版本与安装器聚焦测试，确认绿灯**

Run / 运行：

```bash
python -m pytest -q tests/test_cli.py::test_version_command tests/test_install_script.py
python -m ruff check app/core/settings.py tests/test_cli.py tests/test_install_script.py
python -m ruff format --check app/core/settings.py tests/test_cli.py tests/test_install_script.py
```

Expected / 预期：全部退出 0；安装器调用方式和 launcher 隔离断言保持不变。

- [ ] **Step 6：提交版本和发布文档**

```bash
git add \
  pyproject.toml \
  app/core/settings.py \
  tests/test_cli.py \
  tests/test_install_script.py \
  README.md \
  docs/development-log/2026-08-23-v0-2-trusted-report.md
git commit -m "发布 Garden v0.2.0 可信报告版"
```

- [ ] **Step 7：运行完整自动化门禁**

Run / 运行：

```bash
python -m ruff format --check .
python -m ruff check .
python -m pytest -q
git diff --check origin/main...HEAD
```

Expected / 预期：全部退出 0。任何失败都必须先定位到产生失败的任务，补写能够稳定重现问题的测试，再做最小修复和独立提交；不得忽略失败继续发布。

- [ ] **Step 8：构建 wheel 并验证迁移资源仍可加载**

Run / 运行：

```bash
python -m pytest -q tests/test_migrations.py::test_built_wheel_installs_with_loadable_migration_assets
```

Expected / 预期：退出 0，构建出的文件名匹配 `garden-0.2.0-*.whl`，安装后可以加载 Alembic 资源并初始化数据库。

- [ ] **Step 9：运行真实本机 TCP 端到端扫描**

Run / 运行：

```bash
python scripts/e2e_url_scan.py
```

Expected / 预期：退出 0；JSON 输出中的状态为 `completed` 或 `completed_with_warnings`，必要报告章节和本地夹具资产均存在。

- [ ] **Step 10：用隔离目录验证正式安装器和仓库遮蔽防护**

Run / 运行：

```bash
GARDEN_RELEASE_DIR="$(mktemp -d /tmp/garden-v020.XXXXXX)"
GARDEN_REPO_DIR="$(pwd -P)"
env \
  GARDEN_HOME="$GARDEN_RELEASE_DIR/home" \
  GARDEN_INSTALL_ROOT="$GARDEN_RELEASE_DIR/runtime" \
  GARDEN_BIN_DIR="$GARDEN_RELEASE_DIR/bin" \
  GARDEN_PYTHON="$(command -v python3)" \
  ./install.sh
env GARDEN_HOME="$GARDEN_RELEASE_DIR/home" \
  "$GARDEN_RELEASE_DIR/bin/garden" --version
env GARDEN_HOME="$GARDEN_RELEASE_DIR/home" \
  "$GARDEN_RELEASE_DIR/bin/gardenctl" --version
(cd /tmp && env GARDEN_HOME="$GARDEN_RELEASE_DIR/home" \
  "$GARDEN_RELEASE_DIR/bin/garden" scan --help)
(cd "$GARDEN_REPO_DIR" && env GARDEN_HOME="$GARDEN_RELEASE_DIR/home" \
  "$GARDEN_RELEASE_DIR/bin/garden" --version)
env \
  GARDEN_CLI_RUNTIME=1 \
  GARDEN_HOME="$GARDEN_RELEASE_DIR/home" \
  GARDEN_DATABASE_URL="sqlite+pysqlite:///$GARDEN_RELEASE_DIR/e2e.db" \
  "$GARDEN_RELEASE_DIR/runtime/bin/python" scripts/e2e_url_scan.py
```

Expected / 预期：全部调用退出 0，`garden` 与 `gardenctl` 的版本输出均为 `Garden 0.2.0`；从仓库目录执行时加载隔离 runtime，不加载当前源码树；最后一条命令使用已安装 runtime 完成数据库初始化、本地 TCP 扫描和报告生成。

- [ ] **Step 11：确认分支只包含计划内变更**

Run / 运行：

```bash
git status --short
git log --oneline --decorate origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
```

Expected / 预期：工作区干净；提交按任务分离；无模型、迁移、模板、路由、CLI 参数或网络策略变更；差异检查退出 0。

---

## 计划自审记录

- 规格覆盖：任务 1 冻结兼容契约；任务 2—3 覆盖可信版本来源和南京农业大学夹具；任务 4 覆盖稳定分组；任务 5 覆盖旧记录；任务 6 覆盖发布与安装门禁。
- TDD 顺序：行为改动均先增加能够说明失败原因的测试，再写最小实现，最后运行聚焦回归和完整门禁。
- 类型一致性：保留 `version_hints: list[str]`，只在现有 JSON 中增加可选 `version_hint_details: list[dict[str, str]]`；不改变 ORM 或公开 schema。
- 用户体验：计划不修改模板、路由、CLI 参数、扫描预算、网络策略或状态集合；版本号是唯一预期用户可见变化。
- 占位与外部依赖：计划不存在未完成实现或模糊占位；南京农业大学回归全部使用 `httpx.MockTransport` 和本地 TCP 夹具。

---

## 完成定义

- 所有六个任务按顺序完成，每个行为任务都有可复现的红灯和绿灯证据。
- 南京农业大学本地夹具保存 104 条原始观察，但报告只展示两类重复安全响应头问题。
- 每条新版本线索都带有 `query`、`path` 或 `body_marker` 来源；旧记录不再展示无法验证的正文数字。
- v0.2.0 与基线相比没有 CLI、Web、API、报告结构、扫描边界或状态语义变化。
- 完整测试、Ruff、wheel、迁移资源、端到端扫描和正式安装器冒烟全部通过。
- 本机已安装 Garden 只能在发布分支完成审查、产物通过门禁后更新。
