# Garden v0.3.0 被动认证覆盖对比设计

**日期：** 2026-08-26
**状态：** 已批准、已实施并通过发布前验证
**上位设计：** `docs/superpowers/specs/2026-08-11-authenticated-coverage-platform-design.md`
**版本定位：** 在不改变现有 quick 用户体验的前提下，交付固定三上下文的被动认证覆盖差异；主动权限重放继续关闭。

## 1. 决策摘要

v0.3.0 新增一个独立的高级工作流：对同一个已授权目标，分别以 `anonymous`、`user`、`admin` 三种上下文执行有界被动采集，并按规范化资产身份生成覆盖矩阵。

本版本不改变以下现有入口及其默认行为：

- `garden scan URL`；
- `garden scan --url URL`；
- `POST /api/scans`；
- 首页单 URL 表单；
- quick 扫描详情页和既有 Markdown 资产报告结构。

认证覆盖通过新的 `garden coverage` 命令和 `/api/assessments` HTTP 接口进入。两个适配器调用同一个 `ScanApplicationService.start_assessment(request)` 应用接口，不复制认证、采集或分类规则。

## 2. 目标与非目标

### 2.1 目标

1. 一个高级命令或 HTTP 请求可以启动固定三上下文的被动 assessment。
2. 三种上下文在相同 URL、网络策略和采集预算下执行。
3. 资产通过 `identity_key` 对齐，并输出可解释、可追溯的三列覆盖矩阵。
4. 失败或不完整上下文不会制造“角色不可见”的假象。
5. 报告明确区分上下文健康、覆盖状态、差异分类和普通被动观察。
6. quick 的命令、HTTP、网页和报告体验由兼容性测试锁定。

### 2.2 非目标

- 不执行主动权限重放；
- 不公开 `active_checks_enabled` 或授权确认参数；
- 不支持任意 N 角色；
- 不增加定时扫描、历史基线漂移或外部工单集成；
- 不重新设计首页、quick 详情页或默认导航；
- 不改动安装器的 Python 发现、策略迁移或正式启动器行为；
- 不把覆盖差异自动升级为漏洞 finding。

## 3. 不可破坏的兼容约束

### 3.1 Quick CLI

`garden scan` 的参数名、默认值、前台等待、`--detach`、结果标题、覆盖告警与请求失败分栏保持不变。顶层 CLI 可以新增 `coverage`，但不能改变 `scan` 子命令的帮助和行为。

现有 `ScanOptions.user_agent=Garden-Authorized-Asset-Scanner/0.2` 继续作为 quick 网络兼容标识，本版本不借产品版本升级改变目标侧可观察的 quick 请求身份。

### 3.2 Quick HTTP

`POST /api/scans` 继续接受 `ScanStartRequest` 并返回 `ScanRunView`；`GET /api/scans/{id}`、取消和报告下载路径不变。

### 3.3 Quick Web

首页仍然是单 URL quick 表单；表单字段、提交路径和默认预算不变。v0.3.0 不在首页插入凭证选择器，也不把高级认证工作流设为默认。

### 3.4 Quick 报告

`ScanReportService.generate(session, scan_run_id)` 保持单一接口。内部可以按 `run.mode` 选择渲染实现，但 quick 必须继续使用当前渲染路径，标题、章节顺序和失败分类不因认证覆盖而改变。

## 4. 用户工作流

### 4.1 前置条件

用户已经通过现有命令创建：

- 一个 `Target`；
- 一个 `role=user` 的凭证档案；
- 一个 `role=admin` 的凭证档案；
- 两个能够通过现有会话验证模块验证的认证配置。

两个凭证档案必须属于同一目标，assessment URL 必须与目标 `base_url` 同源。

### 4.2 CLI

交互式终端的默认入口为：

```bash
garden coverage https://app.example/
```

该命令必须进入认证覆盖准备向导，而不是要求用户预先退出并拼接多条 `target`、`cred`、`login` 命令。向导按以下顺序执行：

1. 按 assessment URL 的同源信息查找已有 Target；存在多个时显示编号、名称和 owner 供选择，没有时原地创建；
2. 只显示所选 Target 下角色精确为 `user` 的凭据档案；允许选择已有档案或原地创建；
3. 以相同方式处理角色精确为 `admin` 的凭据档案；
4. 检查登录配置文件存在、可解析且属于支持的 HTTP 或 Playwright 适配器；
5. 显示不含秘密的准备摘要，确认后提交 assessment；
6. 前台模式持续显示三个上下文的登录、会话验证、采集和比较状态。

已有档案完整时，向导仍显示选择和确认步骤，但不重复创建。自动化环境可以显式提供：

```bash
garden coverage https://app.example/ \
  --user-profile 12 \
  --admin-profile 13 \
  --non-interactive
```

`--non-interactive` 缺少任一档案 ID 时立即失败，不等待终端输入。是否交互以显式参数和 TTY 检测共同决定，不能在 CI、管道或重定向输入中悬挂。

可选参数复用 quick 的有界采集参数：`--max-pages`、`--max-resources`、`--max-depth`、`--request-timeout`、`--overall-timeout`、`--retries`、`--detach` 和 `--ui-port`。可选 `--source-run` 只接受同一 target 的终态 quick run。

命令输出上下文健康摘要、分类计数和报告位置。它不接受主动检查开关。

### 4.3 敏感输入生命周期

向导使用隐藏输入读取密码或令牌，必要时二次确认。原始值不进入命令参数、shell history、Rich 渲染对象、普通数据库字段或异常消息。

新建或需要重新补充秘密的凭据档案使用 `ephemeral-file://` 引用：

1. `EphemeralSecretStore` 在 Garden 私有运行目录中原子创建随机文件，目录权限 `0700`、文件权限 `0600`；
2. 解析器只接受该私有目录内的普通文件，拒绝符号链接、目录穿越、宽松权限和目录外路径；
3. 凭据档案只保存不可复用的引用路径，UI/CLI 继续使用统一引用脱敏；
4. `establish_contexts` 在 user/admin 会话建立和验证结束后，于 `finally` 中删除本次向导创建的原始秘密文件；成功、失败、取消都执行；
5. 向导启动和服务启动时清理超过 15 分钟的崩溃遗留文件；
6. 已建立会话继续使用现有受保护会话存储；下一次需要重新登录且临时引用已消费时，coverage 向导重新隐藏提示并轮换引用。

如果用户选择已有 `env://VAR` 引用且变量在 Garden Web 运行进程中可用，向导不创建临时文件。`literal://` 仅保留现有本地 demo 兼容，不由 coverage 向导创建或推荐。

### 4.4 HTTP

```http
POST /api/assessments
Content-Type: application/json

{
  "url": "https://app.example/",
  "user_profile_id": 12,
  "admin_profile_id": 13,
  "options": {
    "max_pages": 50,
    "max_resources": 200,
    "max_depth": 2
  }
}
```

v0.3.0 的公共请求模型不包含主动重放字段。读取接口为：

- `GET /api/assessments/{id}`；
- `GET /api/assessments/{id}/differences`；
- `POST /api/assessments/{id}/cancel`；
- `GET /api/assessments/{id}/report`。

## 5. 深模块与接口

### 5.1 覆盖比较模块

模块位于 `app/services/coverage_comparison.py`，外部接口只有：

```python
CoverageComparisonService.compare(
    session: Session,
    run: ScanRun,
) -> CoverageComparisonSummary
```

调用者不需要知道集合连接、上下文可用性、属性摘要、分类优先级、置信度或幂等重建细节。该接口同时是模块的公共测试接缝。

`CoverageComparisonSummary` 只返回稳定的运行级结果：运行 ID、生成记录 ID 和分类计数；持久化细节通过 `CoverageDifference` 读取。

### 5.2 认证编排模块

`ScanPipeline.execute_authenticated(session, scan_run_id)` 是阶段执行接缝。它复用现有上下文建立、上下文采集、规范化、分析和报告能力，并插入覆盖比较阶段。

`ScanPipeline.execute(session, scan_run_id)` 继续只承载现有 quick 行为，不通过大范围条件分支重写 quick 流水线。

### 5.3 应用接口

`ScanApplicationService.start_assessment(request)` 继续是唯一创建接口。认证模式创建并提交后台执行；quick 的 `start_scan(request)` 兼容接口保持不变。

新增只读接口：

```python
ScanApplicationService.list_coverage_differences(
    scan_run_id: int,
) -> list[CoverageDifferenceView]
```

### 5.4 适配器

- `app/api/routes/assessments.py`：HTTP 适配器；
- `app/cli/coverage.py`：Typer 适配器；
- `app/cli/coverage_wizard.py`：只负责终端步骤、选择、隐藏输入和确认；
- `app/cli/local_api.py`：本机 HTTP 客户端适配器。

适配器只负责校验传输模型、调用应用接口和渲染结果，不包含差异分类实现。

### 5.5 临时秘密深模块

`app/services/ephemeral_secret_store.py` 提供：

```python
EphemeralSecretStore.write(secret: str) -> str
EphemeralSecretStore.read(secret_ref: str) -> str
EphemeralSecretStore.delete(secret_ref: str) -> None
EphemeralSecretStore.purge_expired(max_age_seconds: int = 900) -> tuple[str, ...]
```

调用者不需要了解随机命名、权限、原子写入、路径约束或过期清理。该接口是敏感输入生命周期的公共测试接缝。

## 6. 覆盖状态与分类

### 6.1 上下文状态

对每个 `identity_key` 和每个上下文计算：

- `present`：该上下文已持久化该身份资产；即使上下文后来不完整，已观察到的存在性仍然可信；
- `absent`：该上下文 `collection_status=completed` 且 `completeness=complete`，但没有该身份资产；
- `unknown`：没有该身份资产，并且该上下文失败、缺失、中断或采集不完整。

因此“不完整上下文中已经观察到的资产”可以是 `present`，但“不完整上下文中没有观察到的资产”只能是 `unknown`。

### 6.2 分类优先级

任何一个上下文为 `unknown` 时，整体分类必须是 `unknown`。其余状态按以下顺序分类：

| anonymous | user | admin | 分类 |
|---|---|---|---|
| present | present | present | 属性一致为 `shared`，属性不同为 `inconsistent` |
| absent | absent | present | `admin_only` |
| absent | present | present | `user_only` |
| absent | present | absent | `inconsistent` |
| present | absent/present | 任一缺失 | `unexpectedly_anonymous` |

不存在于任何上下文的身份不会进入比较集合。

### 6.3 属性一致性

只有三个上下文都为 `present` 时比较属性。比较字段固定为：

- `status_code`；
- 规范化、脱敏 URL；
- `title`；
- `attributes.content_type`；
- `attributes.content_signature`。

其中任一稳定字段不同则分类为 `inconsistent`。`context_summaries` 只保存上述脱敏字段和资产 ID，不保存响应正文、Cookie、Authorization、查询值或受保护存储内容。

### 6.4 置信度与诊断

- 完整上下文的存在性差异：`high`；
- 三上下文均存在但稳定响应属性不同：`medium`；
- 包含未知状态：`low`。

诊断使用稳定中文句子说明分类依据，不把 `admin_only`、`user_only` 或 `unexpectedly_anonymous` 直接称为漏洞。

## 7. 阶段与终态

认证覆盖执行：

```text
validate
→ establish_contexts
→ collect
→ normalize
→ compare_coverage
→ replay_authorization(skipped)
→ analyze
→ report
```

规则：

- `replay_authorization` 必须持久化为 `skipped`，摘要说明 v0.3.0 为被动版本；
- 某个认证上下文失败时，其他可用上下文继续采集，后续比较和报告继续执行；
- 上下文不完整时运行终态为 `incomplete`，但仍生成报告；
- 全部上下文完整且无失败时为 `completed`；
- 全部上下文完整但存在非致命覆盖告警时为 `completed_with_warnings`；
- 编排器自身发生致命错误时为 `failed`，未执行阶段明确标记 `skipped`；
- 终态运行清除 `active_key`，允许用户修复凭证后重试。

## 8. 报告

认证报告标题为 `Garden 认证覆盖报告 #<id>`，至少包含：

1. 执行摘要；
2. 三上下文健康与完整性；
3. 覆盖差异分类计数；
4. anonymous/user/admin 三列矩阵；
5. 属性不一致详情；
6. 被动观察与证据索引；
7. 阶段状态、失败和未覆盖部分；
8. 明确声明“差异不是已确认漏洞，主动权限重放未执行”。

`unknown` 必须在矩阵中直观展示，不能渲染为空白或 `absent`。

## 9. 公共测试接缝

TDD 只从以下已定义接缝观察行为，不测试私有辅助函数：

1. `EphemeralSecretStore`：权限、路径约束、消费删除和过期清理；
2. `CoverageSetupWizard.run(request)`：目标/档案选择与创建、隐藏输入、非交互失败和安全摘要；
3. `CoverageComparisonService.compare(contexts)`：分类、unknown 语义、属性不一致、幂等性和脱敏；
4. `ScanApplicationService.start_assessment/get_assessment/list_coverage_differences`：调度、状态和读取；
5. `/api/assessments` 接口族：HTTP 传输协议与主动字段不可达；
6. `garden coverage`：高级 CLI 提交、等待、摘要和错误显示；
7. `ScanReportService.generate(session, scan_run_id)`：认证报告矩阵与 quick 报告兼容；
8. 现有 `garden scan`、`/api/scans` 和首页表单：兼容性门禁；
9. 本地真实认证 fixture：端到端三上下文采集，不访问外部目标。

数据库查询只用于比较模块的持久化结果验证；CLI/API/编排测试不绕过其公共接口查询内部状态。

## 10. 安全与隐私

- 只执行现有被动采集允许的方法和边界策略；
- v0.3.0 公共入口无法开启主动重放；
- quick CLI 不调用 coverage 向导，也不读取、创建或清理 coverage 临时秘密；
- 每个网络连接与重定向继续执行目标策略校验；
- 普通数据库字段、日志、CLI、HTTP、报告不出现可复用会话材料；
- 报告只使用脱敏 URL、标题、内容类型和稳定指纹；
- 上下文错误对外只返回安全诊断，不传播底层异常中的秘密；
- 受保护请求载荷仍沿用现有权限和保留策略，本版本不读取它们做比较。

## 11. 验收标准

- [x] `garden scan`、`POST /api/scans`、首页和 quick 报告兼容门禁通过；
- [x] `garden coverage URL` 在 TTY 中可以选择或原地创建同源 Target、user 档案和 admin 档案；
- [x] 隐藏输入不出现在参数、数据库、日志、CLI、HTTP 或报告中，临时秘密在使用后删除；
- [x] 非交互模式参数不足时立即失败，不等待提示；
- [x] `garden coverage` 可以启动并完成固定三上下文被动 assessment；
- [x] 三上下文资产按 `identity_key` 生成确定、幂等的矩阵；
- [x] 失败或不完整上下文的未观察资产始终为 `unknown`；
- [x] `replay_authorization` 明确为 `skipped`，且没有重放请求；
- [x] 认证报告显示上下文健康、分类计数、矩阵和诊断；
- [x] CLI、HTTP、报告和日志脱敏测试通过；
- [x] 本地真实认证 E2E、全量 pytest、Ruff、format、wheel、迁移和安装回归通过；
- [x] 项目版本更新为 `0.3.0`，文档明确 quick 是默认入口、认证覆盖是高级被动工作流。

## 12. 后续版本

只有 v0.3.x 收敛上下文稳定性、误判和报告解释后，才进入主动权限重放版本。主动重放需要独立设计、显式授权、候选策略、审计、应用级操作者权限和更严格的副作用门禁，不属于本设计。
