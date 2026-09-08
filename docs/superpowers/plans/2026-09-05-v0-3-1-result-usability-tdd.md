# Garden v0.3.1 结果易读性 TDD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一终端、Web 详情页与 Markdown 报告的关注项计数口径，并让常见失败和未覆盖原因带有下一步建议。

**Architecture:** 复用现有 `project_finding_groups()`，在查询结果中计算分类数量，不修改原始发现记录。新增无副作用的展示策略模块，使 CLI、Web、报告共享固定建议文字；展示层不触发网络请求、重试或配置更改。

**Tech Stack:** Python 3.10/3.12、Pydantic、FastAPI、Jinja2、Typer/Rich、pytest、httpx MockTransport。

**Spec:** 本文“确认范围与验收契约”记录本轮对话确认的设计，是本计划随附的需求基线。本计划已获用户批准实施；执行结果与交付状态见文末记录。

## 基线与执行前检查

- 2026-09-05 本地 `main` 为 `3ed0276`，已包含 v0.2 报告分组；本地还保存 `feat/v0-3-passive-auth-coverage` 分支，可用 `git show` 读取 v0.3 实现。
- 报告早已输出 `2 类（104 条原始观察）`，详情也已分组，不再重做这部分。
- 当前 CLI `app/cli/scan.py` 和 Web `app/templates/scan_detail.html` 仍直接展示 `finding_count`。
- 主工作区已有 `.gitignore` 修改及 `.coverage`、`interview.md` 未跟踪文件，保留原状。
- 实施时先只读核对 PR #20 的状态和最终提交。若已合并，从包含该实现的最新 `origin/main` 建立隔离工作树；若未合并，从 PR 的核实 HEAD 建立后续分支，并明确依赖关系，不自行合并 #20。
- 不假设此前临时工作树仍有效；重新验证工作树注册和目录实际内容。
- `v0.3.1` 为计划版本名，版本号和依赖不在本轮功能实现范围内，发布时另行对齐。

## 确认范围与验收契约

### A. 计数口径

1. `finding_count` 保持原义：原始发现记录数；104 仍为 104。
2. `ScanRunView` 新增 `finding_group_count: int | None = Field(default=None, ge=0)`。服务生成的结果必须计算实际值；`None` 只用于兼容缺少该字段的旧响应，不能用默认 0 冒充统计结果。
3. 新响应统一显示 `2 类（104 条原始观察）`，空结果显示 `0 类（0 条原始观察）`。
4. 旧响应字段缺失时显示 `104 条原始观察（分类数未提供）`。
5. 沿用六字段分组规则：标题、类别、严重性、置信度、说明、修复建议；不同修复建议仍分组展示。
6. 原始记录、证据引用、状态、退出码均保持原义。历史 Markdown 文件不自动重写；已有重新生成路径使用当前投影即可。
7. Coverage 的 `shared/user_only/admin_only/unknown` 是覆盖分类，不是安全关注项分类；不能混入 `finding_group_count` 或替换其现有分类统计。

### B. 诊断建议

首批只覆盖下列已存在的结构化代码，精确匹配阶段与代码；未知代码不猜原因，返回无建议。

| 来源与代码 | 固定建议文字 |
|---|---|
| `collect/overall_timeout` | 可增加 --overall-timeout 后重新扫描；重新扫描会创建新任务，不是断点续扫。 |
| `collect/coverage_limit_reached` | 请根据报告中的命中限制检查 --max-pages、--max-resources 或 --max-depth；调整后重新扫描会创建新任务。 |
| `collect/cross_origin_redirect_blocked` | 跳转目标超出当前同源边界；请先确认其是否在授权范围内，如需采集，应以该目标作为新入口单独扫描。 |
| context 的 `authentication_session_unavailable` | 请通过 coverage 向导检查凭据档案、登录地址和登录后验证地址，必要时重新输入凭据。 |
| context 的 `authentication_session_mismatch` | 请检查凭据档案所属 Target 和 user/admin 角色，选择与当前目标匹配的档案后重新提交。 |

- 预算告警不推断哪个限制命中，不解析自由文本，也不凭空生成具体预算数值。
- 建议不插入 URL、异常正文、密码、Cookie、令牌或档案路径。只提供参数名及固定说明，不拼接可执行 shell 命令。
- 对登录失败不直接断言“密码错误”；验证配置和会话状态也可能导致失败。
- CLI 在结束结果后补建议，保持原进度输出；Web 在对应诊断下补建议；报告在对应诊断后补建议。
- CLI 对相同建议只显示一次；Web/报告允许按诊断定位展示。健康任务不出现空建议区块。
- 不修改现有失败分类、retryable、unknown、不完整状态或采集边界；登录过程与后续被动采集的请求方法限制保持既有语义。

## 测试入口（本计划审阅项）

- 应用及 HTTP：`ScanApplicationService.start_scan/get_scan`、`GET /api/scans/{id}`。
- 用户界面：`CliRunner.invoke(app, ["scan", ...])`、`coverage` 命令、`GET /scans/{id}`。
- 报告：`ScanApplicationService.read_report()` 和现有认证覆盖报告生成入口。
- 展示策略：下面定义的两个纯函数，输入结构化计数/错误码，输出用户看到的字符串。
- 使用现有测试数据库；目标网络仅由 `httpx.MockTransport` 模拟。CLI 可以模拟本地 HTTP/进程边界，不模拟分组或建议策略函数。
- 不测试私有方法调用顺序，不通过统计内部调用次数证明展示正确，不访问真实第三方网站。

## 文件与职责

| 文件 | 操作和职责 |
|---|---|
| `app/schemas/scan.py` | 新增可选分类计数字段 |
| `app/services/scan_application.py` | 复用现有分组投影，计算分类数 |
| `app/services/scan_result_presentation.py` | 新建；计数文案和固定诊断建议纯函数 |
| `app/cli/scan.py` | 接入统一计数和结束建议 |
| `app/api/routes/scans.py` | 为模板准备计数文案及诊断建议 |
| `app/templates/scan_detail.html` | 展示新的计数值及建议，保留布局 |
| `app/services/scan_reporting.py` | 使用统一计数文案并附加诊断建议，保留现有分组 |
| `app/cli/coverage.py` | 只添加失败建议，保留覆盖分类统计 |
| `tests/test_scan_result_presentation.py` | 新建；展示策略契约 |
| `tests/test_url_scan_pipeline.py` | API/报告及原始计数一致性 |
| `tests/test_formal_scan_cli.py`、`tests/test_api.py` | CLI/Web 可见输出与兼容性 |
| `tests/test_coverage_cli.py`、`tests/test_authenticated_coverage_reporting.py` | 认证诊断、脱敏和 unknown 回归 |
| `README.md` | 补充计数含义、建议不会自动执行的说明 |

不新增依赖、表、迁移、后台任务或扫描参数。任务 1–2 可以单独验收，任务 3–4 可以另成一个 PR。

## 任务 1：API 同时提供原始数与分类数

**接口：** 输入已有 `ScanRun.findings`，输出 `ScanRunView.finding_count` 和 `finding_group_count`；后续展示都使用这两个字段。

- [ ] RED：扩展 `test_njau_style_report_keeps_104_raw_findings_and_trusted_versions`，沿用现有 52 页面夹具，增加以下断言；不另造一个只模拟统计返回值的服务。

```python
assert scan.finding_count == 104
assert scan.finding_group_count == 2
saved = service.get_scan(scan.id)
assert saved.finding_count == 104
assert saved.finding_group_count == 2
assert "2 类（104 条原始观察）" in service.read_report(scan.id)
```

- [ ] 执行 `pytest -q tests/test_url_scan_pipeline.py -k njau`；预期新增字段缺失导致失败。
- [ ] GREEN：在 schema 新增字段，在现有运行视图投影中加入下列计算。使用已经加载的 findings，避免逐条追加查询。

```python
finding_group_count: int | None = Field(default=None, ge=0)
# 在服务生成运行视图的字典中：
"finding_count": len(run.findings),
"finding_group_count": len(project_finding_groups(run.findings)),
```

- [ ] 每次只补一个下一条用例：零发现返回 `0/0`；不同 remediation 返回两个分组；旧 JSON 缺字段仍能解析且新字段为 `None`；真实 `GET /api/scans/{id}` 输出 `104/2`。HTTP 用例沿用 `_service(tmp_path, handler)` 的真实服务，将其装配为测试 app 的 `scan_service`。
- [ ] 验证读取前后的 findings/evidence 公开计数不变；原有分组、旧报告和 v0.2 兼容测试继续通过。
- [ ] 提交本任务：`feat: expose grouped finding count without changing raw count`。

## 任务 2：CLI、Web、报告使用同一计数文案

**接口：** 新增 `format_finding_count(raw_count: int, group_count: int | None) -> str`；模块不导入数据库、CLI 或网络对象。

- [ ] RED：创建展示策略测试，先实现一个用例并运行失败，再按顺序补充剩余用例。

```python
@pytest.mark.parametrize(
    ("raw_count", "group_count", "expected"),
    [
        (104, 2, "2 类（104 条原始观察）"),
        (0, 0, "0 类（0 条原始观察）"),
        (104, None, "104 条原始观察（分类数未提供）"),
    ],
)
def test_finding_count_text(raw_count, group_count, expected):
    assert format_finding_count(raw_count, group_count) == expected
```

- [ ] GREEN：实现明确的 `None` 分支和正常分支。

```python
def format_finding_count(raw_count: int, group_count: int | None) -> str:
    if group_count is None:
        return f"{raw_count} 条原始观察（分类数未提供）"
    return f"{group_count} 类（{raw_count} 条原始观察）"
```

- [ ] RED → GREEN：分别为 CLI、Web 写可见输出测试后接入函数。CLI 在已有完整命令夹具返回的视图上设 `finding_count=104, finding_group_count=2`，对 `unstyle(result.stdout)` 断言完整文案；Web 通过 `/scans/{id}` 断言相同文字。
- [ ] 报告执行摘要接入同一函数，继续沿用现有 `finding_groups`；现有 `2 类（104 条原始观察）` 测试应保持绿灯，不能为了制造红灯改写已有正确行为。
- [ ] 针对旧响应缺字段重跑 CLI，断言退化文案而非 `0 类`；健康空结果断言 `0 类（0 条原始观察）`。
- [ ] 运行 `pytest -q tests/test_scan_result_presentation.py tests/test_formal_scan_cli.py tests/test_api.py tests/test_url_scan_pipeline.py`。
- [ ] 提交本任务：`feat: align finding counts across result views`。

## 任务 3：Quick 常见诊断提供固定下一步建议

**接口：** 在展示策略模块新增 `diagnostic_hint(stage: str, code: str) -> str | None`。Quick 传实际 stage/code；认证 context 使用显式 stage `context`（任务 4 接入）。函数仅接受这两个参数。

- [ ] RED：从超时这一条开始，不一次写完所有规则。

```python
def test_collection_timeout_explains_new_run():
    assert diagnostic_hint("collect", "overall_timeout") == (
        "可增加 --overall-timeout 后重新扫描；重新扫描会创建新任务，不是断点续扫。"
    )

def test_unknown_code_has_no_speculative_advice():
    assert diagnostic_hint("report", "unrecognized_failure") is None

def test_timeout_advice_does_not_apply_to_an_unrelated_stage():
    assert diagnostic_hint("report", "overall_timeout") is None
```

- [ ] 执行 `pytest -q tests/test_scan_result_presentation.py -k timeout`，确认缺失能力导致红灯。
- [ ] GREEN：用 `(stage, code)` 为键的固定字典实现 `return _DIAGNOSTIC_HINTS.get((stage, code))`；逐条加入本计划表中的 Quick 三项映射及测试。
- [ ] RED → GREEN：扩展现有超时和跨源测试，通过服务返回状态与 `read_report()` 验证建议；原 failure.code、retryable、覆盖告警计数、报告状态保持原值。
- [ ] 在 CLI 完整命令和 Web 详情响应中逐个验证相同建议。CLI 从本次 failures 生成建议并按首次出现顺序去重；不要修改诊断原始记录。
- [ ] 对预算告警仅显示固定检查建议；对未知代码保留原有诊断但不附建议；无故障时无“下一步”空区块。
- [ ] 输入含 `token=TEST_SECRET` 的错误 URL/消息，断言新增建议固定且不含该值；Web 恶意 HTML 字符串保持模板自动转义，Rich 建议输出使用 `markup=False`。
- [ ] 运行 `pytest -q tests/test_scan_result_presentation.py tests/test_url_scan_pipeline.py tests/test_formal_scan_cli.py tests/test_api.py`。
- [ ] 提交本任务：`feat: explain next steps for bounded scan diagnostics`。

## 任务 4：Coverage 认证失败复用诊断建议

**接口：** 复用 `diagnostic_hint("context", context.error_code)`；现有 `ScanContextView.error_code` 和报告的 context 错误码为输入。不新增 context 状态或凭据字段。

- [ ] RED：先添加下面的固定策略用例，再运行 `pytest -q tests/test_scan_result_presentation.py -k authentication`。

```python
def test_authentication_failure_does_not_assume_wrong_password():
    assert diagnostic_hint("context", "authentication_session_unavailable") == (
        "请通过 coverage 向导检查凭据档案、登录地址和登录后验证地址，必要时重新输入凭据。"
    )
```

- [ ] GREEN：加入表中的两项 context 映射，各自完成红绿循环；不为 `context_establishment_failed` 再输出一条重复的总括建议。
- [ ] RED → GREEN：在现有 coverage CLI 的完整命令测试中使用一个失败 user context，断言出现角色名称、原错误码及上述建议；admin 健康时不输出其失败建议。
- [ ] RED → GREEN：扩展认证报告集成测试，断言失败角色及建议可见，同时该不完整上下文缺失资产仍为 `unknown`。
- [ ] 当一个 context 和父任务同时携带相同失败原因时，CLI 只显示一次相同建议；未识别错误码不输出推测性登录指导。
- [ ] 保留并运行现有包含敏感哨兵字符串的认证报告测试，确认新增建议没有引入异常正文或凭据。健康任务的三上下文状态和覆盖分类输出保持原义。
- [ ] 运行 `pytest -q tests/test_coverage_cli.py tests/test_authenticated_coverage_reporting.py tests/test_coverage_comparison.py tests/test_authenticated_coverage_pipeline.py`。
- [ ] README 补充：计数示例、新增 API 字段可选、建议不自动执行、重新扫描会新建任务。
- [ ] 提交本任务：`feat: add contextual guidance to coverage failures`。

## 任务 5：验收与交付

- [ ] 按核实的执行分支运行现有 quick 启动/帮助/前后台/Ctrl+C 兼容测试；只有最终关注项计数及诊断建议是允许的输出变化。
- [ ] 检查真实 API 的旧字段不变、新字段可选；现有客户端忽略新增字段仍正常，旧服务响应能被新 CLI 解析。
- [ ] 不新增迁移，使用既有 migration 和 wheel 安装测试确认打包完整；不为此次展示改动重复建造浏览器或数据库测试框架。
- [ ] 最终运行 `pytest -q`、`ruff check .`、`ruff format --check .`、`git diff --check`；以实际执行结果记录数量，不沿用历史 323 数字。
- [ ] 人工查看同一条任务的 CLI、Web、Markdown 三份结果，核对 `2 类 / 104 条原始观察`；覆盖健康、预算未覆盖、跨源拦截、登录失败四种样例。
- [ ] 审阅 diff，确认没有额外网络请求、自动扩大范围、重试行为变化或敏感输入变化；确认原工作区改动未被带入。
- [ ] 按独立分组创建 PR：计数统一（任务 1–2），诊断建议（任务 3–4），可在同一工作树依次推进；后一个 PR 说明前一个依赖或待其合并后改为以 main 为基线。
- [ ] 推送后核对远端 HEAD 与 CI 结果，给出 PR 链接。合并由用户审批。

## 计划自审结果

- 已落地的报告分组仅复用，保留其回归测试，不重写分组算法。
- 每个实现任务都有可见行为入口、红灯条件、最小实现和绿灯命令。
- 未知分类数使用 `None` 而非 0；原始数与覆盖分类不会混淆。
- 建议映射集中定义，不依赖未经脱敏的异常内容，也不生成带敏感 URL 的命令。
- 任务 1–4 已实施，原始需求保留供对照；实际验证与交付状态见下方执行记录。


## 执行记录（2026-09-08）

- 已在 `/private/tmp/garden-v031-result-usability` 隔离实施，未修改主工作区的既有文件。
- 基线为 PR #20 的 `0768a35`；该 PR 尚未合并，本轮使用依赖分支交付。
- 任务 1–2：已实现 API 可选分类数及 CLI/Web/Markdown 统一文案；分类规则与原始记录保持不变。
- 任务 3–4：已实现五种精确 stage/code 建议及终端去重；不改变请求、重试、凭据处理或覆盖判定。
- 红绿证据：104/2 的服务投影缺失、展示模块缺失、旧响应 None 退化文案、CLI/Web 计数、每条建议映射、CLI/Web/报告建议缺失均观察到失败后通过。
- 测试夹具补充了必填 `created_at`；旧版 API 精确字段契约只增加本轮授权的可选字段，仍严格检查所有旧字段。此兼容调整已独立复核。
- 定向验证：计数及兼容 58 项；quick 建议 66 项；认证及覆盖 43 项通过（集合存在重叠，不相加）。
- 最终全量：`python -m pytest -o addopts='' -q`，350 passed，2 条既有 websockets 弃用警告，103.79 秒。
- `ruff check .`、`ruff format --check .`（196 文件）及 `git diff --check` 通过；全量包含安装打包、迁移和真实浏览器本地端到端测试。
- 浏览器基线在沙箱中启动失败，获准在沙箱外重跑后通过；没有因此修改产品逻辑。
- 人工样例：健康结果 0/0 无建议；同一预算任务在 CLI/Web/Markdown 均为 2 类/104 条；跨源拦截有授权边界建议；认证失败显示 user、原错误码和 unknown，建议不包含凭据。
- 子任务代理中途因额度终止后由主代理接手；最终独立审阅及兼容补丁复核均无发现。
- 未升级依赖、版本号或数据库，未更改默认 quick 的启动流程、参数、进度与退出语义。
- 原有 CLI 诊断正文中的 Rich 标记解释及进程内 CliRunner 日志捕获问题不属于本次新增建议逻辑，本轮未改动；新增建议使用 `markup=False`。
- 交付：待创建计数与诊断两个依赖 PR；不自动合并。仓库 CI 仅匹配面向 main 的 PR，依赖 PR 应在前置合并并改基线后运行 CI。
