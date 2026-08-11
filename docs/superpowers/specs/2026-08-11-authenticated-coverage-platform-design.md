# 认证覆盖差异平台设计

**日期：** 2026-08-11
**状态：** 已批准
**产品决策：** 认证后应用面验证是 Garden 的核心能力；单 URL 被动扫描保留为低门槛入口；下一阶段扩展为固定三上下文的认证覆盖差异平台，并重点实现显式开启的主动权限重放。

## 1. 目标

Garden 要针对一个已授权应用回答三个相互关联的问题：

1. 匿名访客、普通用户和管理员分别能够观察到什么？
2. 三种上下文发现的页面、接口、参数及被动响应属性有何差异？
3. 低权限上下文能否重放高权限上下文观察到的请求，并获得等价访问？

产品继续保留现有的单 URL 工作流，但核心价值从通用漏洞扫描转向认证覆盖、可解释差异、脱敏证据、人工分流和复测。

## 2. 范围

### 2.1 MVP 范围

- quick 和 authenticated-coverage 共用一个持久化运行模型。
- 固定比较 `anonymous`、`user`、`admin` 三种上下文。
- 被动比较页面、接口、参数、状态码、重定向、内容类型、标题和脱敏响应指纹。
- 显式开启后，执行从高权限到低权限的跨角色重放。
- 只重放本次运行实际捕获的 `GET`、`HEAD`、`OPTIONS` 请求。
- 每个差异和重放判定都有结构化、默认脱敏的证据。
- 保留 finding 生命周期、人工 triage、复测和报告导出。
- Web、HTTP API 和 CLI 调用同一应用服务。
- 迁移期保持现有单 URL quick 入口向后兼容。

### 2.2 MVP 不包含

- 自动替换标识符或枚举 ID。
- 重放请求体，以及 `POST`、`PUT`、`PATCH`、`DELETE` 等可能修改状态的方法。
- 破坏性验证、利用载荷、暴力尝试或大范围模糊测试。
- 在首版 UI 或公共协议中支持任意 N 角色矩阵。
- 在没有显式访问策略或人工确认时，自动宣称已确认漏洞。
- 替代 Burp、ZAP、Nuclei 等执行引擎。

内部上下文模型不得阻碍未来扩展到 N 角色，但首版用户协议固定为三种上下文。

## 3. 产品体验

### 3.1 Quick scan

用户提交一个已授权的 HTTP 或 HTTPS URL。Garden 创建 `quick` 运行，仅包含匿名上下文，执行有界的被动采集、分析和报告。

迁移期间，现有 `gardenctl scan --url URL` 和 `/api/scans` 保留为 quick 模式兼容入口。

### 3.2 Authenticated coverage

用户提交：

- 已授权入口 URL；
- 一个普通用户凭证档案；
- 一个管理员凭证档案；
- 有界采集参数；
- 可选的主动重放开关。

Garden 创建一次包含 anonymous、user、admin 三种上下文的运行，验证两个认证会话，在相同边界下分别采集，计算覆盖差异，按配置执行主动重放，生成授权审查候选，并输出统一报告。

### 3.3 从 quick 升级到 authenticated coverage

已完成运行保持不可变。从 quick 结果发起认证覆盖时创建新运行，并通过 `source_run_id` 指向来源 quick 运行。系统可以复用目标配置，但不能把旧观察结果静默当作新三上下文快照的一部分。

## 4. 统一架构

`ScanRun` 升级为唯一的产品级 assessment 记录。UI 和文档统一称为“验证运行”或 assessment；第一轮迁移保留现有类名和表名，降低数据库迁移风险。

```text
Web / API / CLI
       |
       v
ScanApplicationService.start_assessment
       |
       v
ScanRun (quick | authenticated_coverage)
       |
       +-- ScanContext: anonymous
       +-- ScanContext: user
       +-- ScanContext: admin
       |
       +-- 按上下文归属的资产和请求
       +-- 覆盖差异
       +-- 主动重放记录
       +-- 证据和 findings
       +-- 统一报告
```

统一流水线为：

```text
validate
-> establish_contexts
-> collect
-> normalize
-> compare_coverage
-> replay_authorization
-> analyze
-> report
```

未开启主动重放时，`replay_authorization` 必须持久化为 `skipped`，不能从进度和报告中静默消失。

现有 `ScanJob` 降级为旧执行记录。其 inventory、checks、retest 职责迁入 `ScanRunStage`、`ScanContext` 及专门的重放/复测记录。旧表在迁移窗口内保持只读，只有在数据迁移和适配器兼容均验证后才能删除。

## 5. 领域模型

### 5.1 ScanRun

统一父记录新增或明确以下字段：

- `mode`：`quick` 或 `authenticated_coverage`；
- `source_run_id`：可选，指向引导本次运行的 quick run；
- `active_checks_enabled`；
- 授权确认元数据；
- 不可变的执行参数快照；
- 总体状态、完整性、阶段、进度、时间和报告元数据。

### 5.2 ScanContext

每个上下文属于一个 `ScanRun`，包含：

- `kind`：`anonymous`、`user` 或 `admin`；
- 可选的凭证档案和认证会话引用；
- 登录与会话验证状态；
- 采集状态、时间、错误码和错误信息；
- 上下文级采集数量与完整性。

quick run 只有一个 anonymous context；authenticated-coverage run 必须且只能各有一个 anonymous、user、admin context。

### 5.3 ScanAsset

`ScanAsset` 统一表示页面、接口和参数，包含：

- `scan_run_id` 和 `context_id`；
- 资产类型；
- 规范化身份键；
- URL、方法、状态、标题和结构化脱敏属性；
- 发现来源和时间。

现有 inventory page、endpoint、parameter 数据逐步迁入该结构。迁移完成前，旧 inventory 模型只作为兼容读取来源。

### 5.4 ScanRequest

捕获请求包含：

- 来源上下文和资产；
- 方法、规范化 URL、请求头名称元数据和请求指纹；
- 是否允许重放及纳入/排除原因；
- 敏感精确值的受保护存储引用；
- 用于 UI、日志和报告的脱敏展示形式。

普通数据库字段和常规导出不能包含原始 Cookie、Authorization 或其他可复用会话材料。

### 5.5 CoverageDifference

差异记录包含：

- 规范化资产身份；
- 是否存在于 anonymous、user、admin；
- 各上下文的状态、重定向、内容类型、标题和脱敏内容指纹；
- shared、user-only、admin-only、unexpectedly-anonymous、inconsistent 等分类；
- 比较置信度和解释性诊断。

### 5.6 ReplayExecution

重放记录包含：

- 来源请求和来源上下文；
- 目标上下文；
- 精确的重放策略快照；
- 状态、时间、重定向链、响应指纹和脱敏证据；
- `blocked`、`equivalent_access`、`changed_response` 或 `inconclusive` 判定；
- 判定依据说明。

### 5.7 Finding 与 Evidence 收敛

统一 finding 模型关联被动差异、重放执行、资产和证据，并保留严重性、置信度、状态、去重、首次/最后发现时间、复测和导出能力。

统一 evidence 模型保存结构化脱敏预览和受保护存储引用。迁移完成后删除 quick scan 与旧认证流程中并行的 evidence、finding 和 report 实现。

## 6. 规范化身份与被动比较

被动资产身份键使用：

```text
HTTP 方法 + 规范化路径 + 排序后的参数名称集合
```

参数值不参与被动身份匹配。规范化需要移除 fragment、统一默认端口、保留有意义的路径结构，并对尾部斜杠和重复查询键采用明确策略。

针对每个身份，Garden 比较：

- 上下文存在性；
- HTTP 状态；
- 最终 URL 与重定向类别；
- 内容类型；
- 页面标题；
- 稳定的脱敏内容指纹；
- 已发现参数名称和响应标记。

时间戳、nonce、请求 ID、会话相关文本等动态值必须在稳定指纹中规范化或排除。上下文缺失或执行失败时，结果是 unknown，不是 absent。

## 7. 主动权限重放协议

### 7.1 启用条件

主动重放默认关闭。运行必须持久化显式 opt-in 和授权确认。候选选择、跳过、执行和判定均写入审计记录。

在 Garden 具备应用级操作者认证和主动重放权限前，面向网络的 Web/API 不得启用主动重放。本地 CLI 仅能通过明确确认参数开启。

### 7.2 候选方向

MVP 仅生成以下从高权限到低权限的候选：

- `user -> anonymous`；
- `admin -> user`；
- `admin -> anonymous`。

anonymous 到认证上下文、user 到 admin 的差异由被动比较覆盖，不生成主动授权候选。

### 7.3 候选资格

重放候选必须：

- 来源于本次运行；
- 方法为 `GET`、`HEAD` 或 `OPTIONS`；
- 指向已准入的同源目标；
- 已通过 URL、DNS、重定向和目标策略校验；
- 位于候选数量、请求次数、并发、响应大小和总时间限制内；
- 不包含请求体；
- 不要求修改标识符。

该协议不接受用户任意输入的重放 URL。

### 7.4 请求构造

Garden 保留捕获请求的方法、路径和查询值，丢弃来源上下文的 Cookie、Authorization、代理认证和 hop-by-hop headers，再应用目标上下文已验证的会话。只有显式 allowlist 中的安全表示性请求头可以复制。

重定向由系统手动跟随，每个目的地址都重新校验。跨源或被拒绝的重定向会根据已观察到的响应终止为 blocked 或 inconclusive。

默认每个来源—目标角色对只重放一次。自动重试关闭；未来若允许，也只能针对传输失败，并记录每次尝试。

### 7.5 判定

- `blocked`：401/403、已识别登录跳转、认证失败标记或其他明确拒绝。
- `equivalent_access`：低权限上下文获得成功响应，且与高权限响应高度相似。
- `changed_response`：请求成功，但授权相关响应属性存在实质差异。
- `inconclusive`：会话失效、超时、内容不稳定、策略拒绝或证据不足。

`equivalent_access` 生成“疑似授权缺陷” finding。在人工 triage 或显式访问策略证明目标上下文应被拒绝前，不能标记为已确认漏洞。

## 8. 安全、存储与审计

- 现有 HTTP/HTTPS、DNS、重定向、代理、目标准入、超时、并发、页面、深度和响应大小限制应用于每个上下文和每次重放。
- 主动重放不能扩大运行创建时确定的目标范围。
- 会话材料和精确重放值使用受保护存储，要求严格权限、原子写入、过期、删除及可接入加密的接口。
- UI、CLI、日志、审计事件、证据预览和报告统一使用中央脱敏服务。
- 运行保存授权确认、可用时的操作者身份、配置快照、重放数量和所有跳过原因。
- 在认证覆盖能力暴露到 loopback 之外前，demo routes 和 demo credentials 必须只在显式 demo 环境启用。
- 在推荐多人或多实例部署前，必须具备持久化任务执行与重启恢复。

## 9. 失败与完整性语义

- user 或 admin 登录失败时，authenticated-coverage run 标记为 `incomplete`。
- 未知上下文数据不能解释为资产缺失或授权差异。
- 单个采集失败产生上下文 warning，保留其他结果。
- 重放阶段失败不丢弃被动结果，但报告必须声明主动验证不完整。
- 重放前目标上下文会话无效时，跳过所有相关候选并记录，不得使用旧会话继续。
- 即使诊断报告也生成失败，原始阶段失败仍是权威状态。
- 运行状态包括 `completed`、`completed_with_warnings`、`incomplete`、`failed`，每种状态都必须给出完整性说明。

queued/running 工作在 worker 重启后必须恢复，或明确进入可重试的 interrupted 终态，不能因陈旧 idempotency key 永久卡住。

## 10. 用户界面

### 10.1 Web

首页提供两个入口卡片：

- Quick scan：URL 和有界扫描控制。
- Authenticated coverage：URL、user profile、admin profile、有界控制、主动重放 opt-in 和授权确认。

assessment 详情页展示：

- 总体进度和完整性；
- 三个上下文的登录、采集和资产数量；
- 三列覆盖矩阵；
- 重放候选与判定；
- findings、evidence、失败和未覆盖原因；
- 统一报告。

Web 继续采用轻量服务端渲染，不引入重型 SPA。

### 10.2 HTTP API

规范 API 为：

```text
POST /api/assessments
GET  /api/assessments/{id}
GET  /api/assessments/{id}/contexts
GET  /api/assessments/{id}/differences
GET  /api/assessments/{id}/replays
GET  /api/assessments/{id}/report
```

兼容窗口内，`POST /api/scans` 映射到 quick assessment。兼容响应包含规范 assessment ID 和弃用提示，但不能破坏现有响应协议。

### 10.3 CLI

规范命令为：

```bash
gardenctl assess start --url URL --mode quick

gardenctl assess start \
  --url URL \
  --mode authenticated-coverage \
  --user-profile USER \
  --admin-profile ADMIN \
  --enable-active-replay \
  --confirm-authorized
```

迁移期间，`gardenctl scan --url URL` 保留为 quick 模式别名。

## 11. 报告与 Findings

统一报告包含：

1. 执行摘要和授权范围；
2. 运行模式、参数快照和完整性；
3. anonymous、user、admin 会话有效性；
4. 三上下文覆盖矩阵和重要差异；
5. 主动重放范围、判定、跳过原因和证据；
6. 疑似授权 findings 与 triage 状态；
7. 被动 findings；
8. 失败、限制和未覆盖原因；
9. 可用时的复测历史；
10. 生成和追溯元数据。

报告必须区分观察事实、自动推断和人工确认结论。二进制响应体和可复用会话材料不能嵌入报告。

## 12. 迁移策略

1. 在修改持久化模型前引入 Alembic。
2. 新增统一运行字段及 context、request、difference、replay 表，不删除旧表。
3. 让新 quick run 走统一模型，同时保持现有适配器兼容。
4. 增加三上下文采集和被动比较。
5. 在本地 CLI opt-in 与功能开关后增加主动重放。
6. 增加规范 assessment API、UI、CLI 和报告路径。
7. 通过可重复迁移测试迁移旧 inventory、evidence、finding、job 引用。
8. 读取路径切换到统一记录，保留一个兼容窗口的旧数据只读能力，然后移除旧路径。

历史已完成运行保持不可变。迁移不能把不完整旧数据重新解释成完整三上下文覆盖。

## 13. 实施工作包

### WP1：产品与协议统一

- 对齐 README、PLANS、architecture、threat model、deployment、demo 和 migration 文档。
- 定义规范术语、状态、API schemas 和兼容策略。

### WP2：迁移与统一模型基础

- 引入 Alembic。
- 扩展 `ScanRun`，增加 `ScanContext`、`ScanRequest`、`CoverageDifference`、`ReplayExecution`。
- 增加受保护存储和统一 evidence/finding 引用。

### WP3：统一编排与 quick 兼容

- 实现新阶段图。
- 通过适配器保持现有 quick Web/API/CLI 行为。
- 增加 interrupted run 处理和持久化 dispatcher 契约测试。

### WP4：三上下文采集与被动差异

- 验证 user/admin 会话。
- 在相同限制下采集三个上下文。
- 规范化资产并计算覆盖矩阵。

### WP5：主动重放引擎

- 选择符合条件的候选。
- 用目标上下文替换来源认证。
- 应用网络与执行预算。
- 生成判定、证据、审计事件和疑似 findings。

### WP6：产品界面与生命周期

- 增加 assessment Web、API、CLI。
- 增加统一报告、triage、retest、export。
- 在 Web/API 启用主动重放前增加操作者授权。

### WP7：旧模型迁移与移除

- 迁移历史引用。
- 兼容验证后移除旧 `ScanJob` 及平行 inventory、evidence、finding、report 路径。
- 更新内部包 README 和生成文档。

### WP8：运行加固

- 增加持久化执行与重启恢复。
- 增加数据保留、会话/重放安全存储、可观测性和非 root 部署。
- 按环境隔离 demo routes 和 credentials。

## 14. 测试策略

实现必须包含：

- schema upgrade、rollback 和历史数据迁移测试；
- 现有 quick API/CLI 兼容测试；
- 身份规范化和动态内容过滤单元测试；
- 覆盖分类及所有重放判定单元测试；
- 拒绝危险方法、请求体、跨源目标、被拒绝重定向、任意重放 URL 和来源凭证泄漏的测试；
- 登录失败、会话过期、部分采集、部分重放和 incomplete 报告测试；
- 三种重放方向集成测试；
- user/admin 的真实 Chromium 登录、inventory 和 evidence 路径；
- 包含明确允许/拒绝资源的本地端到端 fixture；
- 证明 queued/running 工作恢复或明确终止的重启测试；
- 数据库视图、UI、CLI、审计事件和导出的脱敏测试；
- finding 去重、triage、retest 和报告回归测试。

CI 保留 Ruff、格式、Python 版本矩阵、Docker build 和 CLI smoke，并新增真实浏览器任务，不能继续只依赖 mock/contract 测试。

## 15. 完成定义

首版认证覆盖发布完成时必须满足：

- 现有单 URL quick 入口保持可用；
- 一个 Web 表单、API 请求或 CLI 命令可启动三上下文 assessment；
- user/admin 会话经过验证，三个上下文在相同边界下完成采集；
- 报告清楚说明每个上下文观察到了什么、哪些资产存在差异；
- opt-in 主动重放覆盖三种已批准的高到低权限方向；
- 每次重放均有界、同源、非修改性、脱敏且可审计；
- 不完整采集不能报告为授权差异；
- 疑似授权 findings 关联可复现脱敏证据，并支持 triage/retest；
- Web/API 主动重放受应用权限控制，本地 CLI 必须显式确认授权；
- 数据库迁移、向后兼容、真实浏览器 E2E、重启恢复、lint、format 和全量测试通过；
- README、architecture、threat model、deployment、demo、migration 文档一致表述“认证验证为核心、quick scan 为入口”；
- 普通数据库视图、日志、UI、CLI 和报告中不出现可复用原始会话材料。

## 16. 后续扩展

MVP 验证后，可增加任意角色矩阵、显式路由访问策略、经过审批的状态修改请求模板、持续调度、baseline drift、外部扫描器执行适配器，以及工单/通知集成。这些能力必须复用统一 run、context、evidence、finding 协议，不能再次产生平行工作流。
