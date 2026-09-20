# Garden

> 提交一个入口 URL，自动完成资产发现、证据归纳和报告生成。

[garden-ctl.com](https://garden-ctl.com/) · [架构说明](docs/architecture.md) · [部署文档](docs/deployment.md)

Garden 是一个面向已授权目标的被动资产扫描与报告系统。用户不需要手动串联登录、inventory、checks 或 report 命令；Web、HTTP API 和 CLI 都调用同一个核心应用服务。

```text
URL
  → 输入与网络校验
  → 同源目标发现
  → 资产与证据收集
  → 结果标准化
  → 被动分析
  → Markdown 报告
```

## 正式 CLI 快速开始

macOS、Linux 或 WSL（Python 3.10+）可直接在仓库根目录安装用户级 CLI；不需要 `sudo`，也不需要手动激活虚拟环境：

```bash
./install.sh
garden --version
garden scan http://127.0.0.1:13000/
```

首次运行会自动启动或复用仅监听本机回环地址的 Web UI，并输出首页和本次扫描详情地址；默认前台显示进度，但不会自动打开浏览器。用户数据默认保存于 `~/.garden`，可用 `GARDEN_HOME` 覆盖。若安装后找不到命令，请按安装器输出将 `~/.local/bin` 加入 PATH。

安装器会自动寻找满足 3.10+ 的解释器；若通用 `python3` 是 3.10/3.11，但系统已有版本化的 Python 3.12/3.13，则优先使用较新的版本化解释器，否则继续兼容通用解释器。安装器也会识别常见 Homebrew 版本化安装路径。需要固定解释器时可设置 `GARDEN_PYTHON=/path/to/python3`，显式指定始终优先。正式命令使用 runtime 内随环境一起替换的隔离入口加载已安装包，即使从 Garden 仓库目录执行，也不会被当前源码覆盖。

```bash
garden scan http://127.0.0.1:13000/ --detach  # 仅提交，立即返回
garden stop                                   # 中断活动扫描并停止本机 UI
```

`gardenctl` 是兼容别名，既有命令组和 `gardenctl scan --url URL` 均继续可用。

## 被动认证覆盖（高级工作流）

默认入口仍是 quick scan。`garden scan` 和 `/api/scans` 保持直接提交；首页表单先预览、再确认启动匿名扫描。需要比较匿名、普通用户和管理员三个登录上下文时，使用独立入口：

```bash
garden coverage URL
```

在交互式终端中，向导会按 assessment URL 的同源信息选择或创建 Target，再分别选择或创建角色精确为 `user` 和 `admin` 的凭据档案。新凭据的敏感值使用隐藏输入；原始值只写入权限为 `0600` 的短生命周期文件，档案只保存 `ephemeral-file://` 引用，并在会话建立与验证后删除该临时文件。密码、令牌和 Cookie 不会进入命令参数、普通数据库字段、日志或报告。

向导在提交前展示入口同源范围、两身份档案以及实际生效的页面数、深度、超时和重试预算，可返回调整或取消。配置检查只读取本地记录与登录配置，不查询 DNS、不访问目标，也不代表登录已验证或覆盖完整。可能联网的会话验证、刷新与登录在确认之后进行；已有有效会话仍可复用。

自动化环境必须显式提供两个档案，缺少任一参数会立即失败而不会等待输入：

```bash
garden coverage https://app.example/ \
  --user-profile 12 \
  --admin-profile 13 \
  --non-interactive
```

coverage 固定执行 `anonymous`、`user`、`admin` 三上下文的有界被动采集。主动权限重放未执行；报告中的覆盖差异不是已确认漏洞，需要结合业务授权模型复核。上下文失败或不完整时，未观察到的资产显示为 `unknown`，不会被误报为 `absent`。

## 提交前预览

Web 首页输入地址后，可展开“预算选项”，点击“预览扫描”检查匿名范围与实际预算，再选择“返回修改”或“开始匿名扫描”。预览不会创建任务；表单无需 JavaScript。输入错误在对应字段显示。确认凭证有效期为 10 分钟，参数或运行配置变化、服务重启后需要重新预览；多进程部署中切换工作进程也可能要求重新预览。

- “配置匹配”只表示本地可检查的配置关系符合要求；尚未检查连通性，认证覆盖尚未验证本次登录，覆盖结果仍未产生。
- 普通扫描仅使用匿名上下文，`max_resources` 限制静态资源采集。认证覆盖按上下文应用页面和深度预算，`max_resources` 为已登录上下文的请求记录上限，不是整个任务的网络请求总量。
- 登录、验证、重定向和重试可能产生额外请求；预算上限不是保证采集量。
- 预览摘要对入口查询值脱敏；返回修改保留原始表单值。密码、Cookie、凭据引用和登录配置原文不进入预览摘要。
- CLI 非交互模式及原有 `/scans`、`/api/scans`、`/api/assessments` 直接提交接口不要求预览凭证。

## 沿用历史配置重新扫描

已结束的匿名任务在结果页提供“沿用配置重新扫描”：带入原始入口与当时实际预算 → 修改 → 预览 → 确认创建新任务。新任务通过 `rerun_of_run_id` 关联来源，结果页可回看原任务；原任务、证据和报告保留。此关联表示配置来源，不表示两次结果已做差异比较。

- 运行中的任务不可沿用；认证覆盖仍使用 `garden coverage` 检查身份并重新提交，不会被降级为匿名扫描。
- 历史预算缺失时逐项提示补齐，不自动套用当前默认值；合法的 `0` 值原样保留。调整预算不会自动扩大范围，最终生效值仍须预览确认。
- 预览与编辑不创建任务、不查询 DNS 或访问目标。确认时再次检查来源；来源配置、参数变化或凭证过期需重新预览。相同来源与配置的在途任务仍会复用，完成后再次提交可创建新任务。
- `source_run_id` 仍专用于匿名任务升级认证覆盖，与重新扫描来源分开保存。

本轮新增数据库迁移 `0003`。升级代码后，启动前执行 `gardenctl db upgrade`；迁移为历史任务保留空的重新扫描来源，不补造关联。

## 与上次扫描对比

通过“沿用配置重新扫描”创建的匿名任务结束后，可在结果页点击“与上次对比”。页面先说明两次入口、Target、预算、请求配置和覆盖情况是否可比，再按原始观察展示变化，并链接到两次观察与已存证据索引。只读取持久化记录，不启动扫描、不查询 DNS 或访问目标。接口为 `GET /api/scans/{id}/comparison`，来源固定为该任务的 `rerun_of_run_id`。

| 状态 | 判定与限制 |
| --- | --- |
| 新增观察 | 本次有记录；两次可比且上次实际采集过对应资产，但没有该观察。 |
| 持续观察 | 两次均存在可验证的同规则、同位置观察，记录 ID 可以不同。即使整体可比性受限，仍可保留这类已观察到的匹配。 |
| 未再观察到 | 上次有记录；两次可比且本次实际采集过对应资产，但未出现该观察。**不代表已修复。** |
| 无法判断 | 范围或配置变化、覆盖或分析不足、失败、截断、历史信息缺失、关联无法核对，或另一轮未采集对应资产。 |

匹配使用已有规则去重标识与经过归属核验的精确证据来源 URL；资产 URL 已脱敏，不单独用作精确位置依据。若一个资产合并了多个精确来源，或只剩脱敏位置，则保守判为无法判断。不同查询值不会因显示脱敏而被合并。页面不展示查询值、Cookie、认证头或响应原文，User-Agent 只显示是否记录及是否变化。对比计数以原始观察的对应关系为单位，不是首页的关注项分类数。两次都没有观察记录也不代表目标安全。

首期仅支持两次均已结束的被动匿名任务，不支持任意历史任务组合、跨身份对比或趋势图；对比不会改写任务、证据或报告，也不生成漏洞修复结论。本轮不新增数据库迁移。

## 仓库开发快速开始

环境要求：Python 3.10+。

```bash
git clone https://github.com/Moxxkidd/Garden.git
cd Garden

python3 -m venv .venv
source .venv/bin/activate
make install

cp .env.example .env
make dev
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，输入一个已授权的 HTTP/HTTPS URL，然后点击 **Start scan**。

页面会自动展示：

- 当前阶段和真实进度
- 已发现的资产、证据和关注项数量
- 重试、部分失败和未覆盖原因
- 最终报告的在线阅读与下载入口

使用 Docker：

```bash
cp .env.example .env
docker compose up --build
```

## 一次提交的三种入口

### Web

访问 `http://127.0.0.1:8000/`，只需填写 URL。后续流程由系统自动执行。

### CLI

```bash
garden scan http://127.0.0.1:13000/
gardenctl scan --url http://127.0.0.1:13000/  # 兼容写法
```

可以用边界参数控制扫描规模：

```bash
garden scan http://127.0.0.1:13000/ \
  --max-pages 50 \
  --max-resources 200 \
  --max-depth 2 \
  --request-timeout 5 \
  --overall-timeout 90 \
  --retries 1
```

--overall-timeout bounds target network collection. When it expires, Garden stops new requests, marks coverage incomplete, and finishes local normalization, analysis, and report generation for evidence already collected.

### HTTP API

```bash
curl -X POST http://127.0.0.1:8000/api/scans \
  -H 'content-type: application/json' \
  -d '{"url":"http://127.0.0.1:13000/"}'
```

相关接口：

- `POST /api/scans`：提交 URL
- `GET /api/scans/{id}`：读取进度、阶段和失败信息
- `POST /api/scans/{id}/cancel`：中断尚未结束的扫描并保留已写入结果
- `GET /api/scans/{id}/report`：阅读或下载报告

## 如何判断结果

CLI、扫描详情页和 Markdown 报告使用相同计数口径，例如 **2 类关注项，6 条原始观察**。原始观察保留独立证据；按已有规则归并的类别用于阅读，不等同于确认漏洞数量。覆盖矩阵的分类计数单独展示。

扫描详情页顶部按执行状态、覆盖情况、关注项和建议操作展示结果摘要。诊断直接可见；已结束任务的执行阶段和原始报告默认折叠，可按需展开，报告下载入口始终保留。运行中的任务保持阶段展开并自动刷新。

**执行进度 100% 不代表覆盖完整。** 请同时查看任务状态和覆盖说明：

- 正常普通扫描：本次范围内采集完成，仅包含匿名上下文。
- 正常认证覆盖：本次范围内匿名、普通用户、管理员三上下文采集完成。
- 达到采集限制、请求失败或缺少身份：提示覆盖不完整，并保留诊断和已有结果。
- 尚在执行或旧响应缺少完整性字段：提示尚未确定或未知，不默认显示为完整。

“本次范围内”受入口、同源边界与采集预算约束；即使完整采集且没有关注项，也不能据此判断整个目标安全。`absent` 表示本次完整上下文内未观察到，`unknown` 表示上下文不完整、无法判断。

认证覆盖在生成最终报告前确定完整性，保证新生成报告与最终评估 API 一致。历史报告不会自动重写。API 保留原始 `finding_count`，增加可选的 `finding_group_count`；普通扫描响应也提供原有运行记录的 `completeness`，其中 `legacy_single_context` 表示普通扫描的兼容模式，需结合任务状态判断采集情况。旧响应缺少字段时保持可读的未知状态。

## 报告内容

超时、预算未覆盖、跨源拦截及常见认证上下文失败会附固定的“下一步”建议。
这些建议不会自动执行、重试或扩大采集范围，也不包含凭据或带敏感参数的命令。
调整参数后重新扫描会创建新任务，不是断点续扫；跨源目标须确认授权后作为新入口单独扫描。
未识别错误码保留原诊断，不猜测原因；认证失败不直接等同于密码错误。

报告由持久化的结构化数据生成，不解析或拼接 CLI 日志，包含：

- 执行摘要与扫描范围
- 发现的资产及关键属性
- `page`、`stylesheet`、`script`、`image`、`document` 分类资产
- 来源明确且默认脱敏的证据；静态资源使用大小、SHA-256、带可信上下文的版本线索和安全信号摘要
- 已观察到的正向安全控制，例如 HSTS 和 X-Frame-Options
- 风险或关注项
- 阶段完成情况和扫描覆盖范围
- 分开列示的覆盖告警与请求失败；覆盖告警包含候选数、已请求数、未覆盖分类、命中限制和代表样本
- 报告生成时间

默认输出目录：

```text
exports/scan-reports/scan-<id>.md
```

## 安全边界

Garden 只应用于已获得授权的目标，并采用以下有界策略：

- 仅允许 `http` 和 `https`
- 只执行有界、同源的被动 `GET` 请求
- 每次连接和重定向都会重新校验目标地址
- 默认允许已授权的公网目标和本机回环目标
- 如需恢复仅本机模式，可显式设置 `GARDEN_ALLOW_NON_LOCAL_TARGETS=false`
- RFC1918/ULA 内网目标还需要设置 `GARDEN_ALLOW_PRIVATE_TARGETS=true`
- link-local、云元数据常用地址、组播和未指定地址始终拒绝
- 请求超时、整体超时、并发、重试、页面数、资源数和深度均有限制
- 页面与静态资源使用独立预算，默认优先采集最多 50 个 HTML 页面，再采集最多 200 个静态资源
- 非文本响应只记录类型和大小，不把二进制内容写入报告

## 核心架构

```text
Web / API / CLI
       ↓
ScanApplicationService.start_scan(url, options)
       ↓
Persisted ScanRun + six-stage ScanPipeline
       ↓
Assets / Evidence / Findings / Failures
       ↓
ScanReportService
```

CLI、路由和页面模板只是适配层，核心业务流程位于应用服务和流水线中。单个阶段或页面失败会被持久化并显示在任务和报告中，不会静默伪装成完整结果。

认证覆盖使用独立的 `start_assessment` / `execute_authenticated` 链路，在 quick 六阶段之外建立并比较固定三上下文；`garden scan` 仍使用原有 quick 链路。

需要登录态、人工 triage、retest 或高级证据生命周期时，原有高级工作流仍然可用，但它们不是 URL 自动扫描的前置步骤。迁移说明见 [docs/legacy-cli-migration.md](docs/legacy-cli-migration.md)。

## 测试

```bash
make test
make lint
```

端到端测试脚本：

```bash
.venv/bin/python scripts/e2e_url_scan.py
```

## 文档

- [架构与执行链路](docs/architecture.md)
- [安装与部署](docs/deployment.md)
- [威胁模型与网络策略](docs/threat-model.md)
- [旧 CLI 迁移说明](docs/legacy-cli-migration.md)
- [重构验收清单](docs/url-scan-refactor-checklist.md)

## License

Garden 当前未声明开源许可证。复用或分发前请先联系仓库所有者。
