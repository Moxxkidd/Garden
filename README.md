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

## v0.6.0：独立身份采集与已知缺口补采

从 Web 导航进入「身份与采集」：选择任一账号或匿名即可采集，不要求管理员账号。多个身份复用 HTML、Sitemap、JS 线索、URL/OpenAPI 与 Hash 路由发现能力，观察按稳定身份 ID 隔离，在同一任务清单中归并资产主体。

1. **准备身份**：创建档案，使用现有自动登录配置，或人工登录／导入 Playwright 状态。验证码、短信、扫码由用户完成；登录窗口位于 **Garden 所在电脑**，远程部署可导入状态。
2. **确认恢复**：保存前用新浏览器恢复，验证指定成功元素或文本。HTTP 200、Cookie 或非登录地址本身都不足以证明登录成功；成功后不会自动扫描。
3. **预览采集**：选择身份、目标和入口，预览不会访问目标。单独确认后开始有界 GET/HEAD 采集；每 20 次允许请求或 30 秒及批次结束时检查身份，验证流量也占用请求预算。
4. **阅读清单**：矩阵区分「已观察／身份不确定／未观察／未知」，保留不同身份的 HTTP 状态与证据。认证诊断不计入已确认身份下观察到的资产主体；这些主体仍不等于业务有效资产。
5. **重新认证与补采**：重新导入或登录后，从原任务检查点预览已知缺口并创建关联子任务。原报告不变；仅补已知未完成／身份不确定请求，不代表全站恢复。

本模式输出资产清单，不执行风险分析；结果摘要明确显示「未执行风险分析」。历史已确认响应与后续身份不确定响应分别保留，不能将后者当成该身份下的有效观察。

```bash
# 单身份或匿名，不要求 user/admin 成对提供
garden identities collect https://example.test/ --target-id 1 --profile-id 2
garden identities collect https://example.test/ --target-id 1 --anonymous
# 导入显式指定的本地文件；状态不进入命令行参数
garden identities import state.json --profile-id 2 \
  --login-url https://example.test/login --validate-url https://example.test/me \
  --success-selector '#account-menu'
garden identities validate 7
garden identities revoke 7
garden identities recover 12 4 8
```

人工登录的 CLI 子命令 `login / status / confirm / cancel` 通过 `--api-url` 连接正在运行的 Garden Web 服务，由服务持有浏览器窗口，避免 CLI 退出导致状态丢失。`activate PROFILE_ID` 显式使用现有自动登录配置并验证恢复。运行 `garden identities --help` 查看参数。

升级前运行 `garden db upgrade`，当前迁移为 `0008`。兼容旧匿名及固定 anonymous/user/admin 覆盖流程；有新增身份数据时拒绝有损降级。导入上限 1 MiB，支持同源 Cookie、localStorage 和 sessionStorage，不保证 IndexedDB、设备绑定凭据或任意跨域 SSO 状态可以迁移。人工窗口最多同时 3 个，15 分钟过期；补采检查点最多保存 2000 条已知请求及 1 MiB URL 材料，截断会明确显示。

[真实本地 v0.6 演示与验证记录](docs/demo-v06-identities.md)

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

## 本地环境自检

启动或扫描遇到问题时，先运行：

```bash
garden doctor
garden doctor --json
garden doctor --ui-port 8001
```

`doctor` 检查当前安装版本、Python 解释器、模块来源和 PATH 入口，说明配置来源，并检查 Chromium 文件、本地 SQLite 迁移版本、回环端口及报告/存储/运行目录权限。输出统一使用「正常」「需要处理」「无法确认」，附对应的下一步建议；`--json` 提供稳定的检查 ID、`schema_version`、`checks` 和 `exit_code`。

- 正式安装读取 `GARDEN_HOME/config.env`，开发模式读取当前工作目录 `.env`；环境变量优先。输出不包含配置内容、数据库连接串、代理凭据或原始异常。
- 诊断不会创建目录或数据库、执行迁移、安装浏览器、启动/停止服务、删除状态文件或访问扫描目标。浏览器路径通过短暂的 Playwright 驱动查询，未启动浏览器验证系统依赖。
- 本地服务仅探测 `127.0.0.1` 的 TCP 端口；有监听不等于已确认 Garden 身份或健康状态，doctor 不发送 HTTP 请求。默认检查状态文件中的端口，没有状态文件时使用配置端口；损坏记录保留未知。`--ui-port` 可指定检查端口。
- 数据库检查只支持普通本地 SQLite 文件；远程数据库、内存数据库、带 URI 选项或存在 WAL/事务日志的文件保留「无法确认」。迁移声明只读取、不执行，未知版本不会被建议强行升级或降级。
- 权限通过不代表磁盘空间充足；目录不存在时显示「无法确认」，不会为了测试而创建文件。多份 PATH 入口会提示核对，不自动修改 PATH。

退出码：`0` 表示已检查项目均正常；`1` 表示存在需要处理项；`2` 表示没有已确认的处理项，但仍有无法确认的项目。命令参数错误也按 CLI 惯例返回 `2`。请先核对当前安装与数据目录，再使用同一安装的 `gardenctl db upgrade` 等建议命令。

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

## v0.4.0：统一资产清单

扫描结果页与 inventory 详情页提供“查看统一资产清单”，顶部导航也可进入 `/assets`。选择来源任务后，可以按页面、接口、静态资源、文件、身份和观察状态筛选，搜索脱敏 URL/标题/方法，排序、分页，并下载全部匹配记录的 JSON/CSV。

```bash
# N 为已存在的任务 ID；scan 同时包含匿名扫描与认证覆盖
garden assets list --source scan --run-id N
garden assets list --source scan --run-id N --kind endpoint --context user --json
garden assets list --source inventory --run-id N --page-size 50
garden assets export --source scan --run-id N --format json --output assets.json
garden assets export --source inventory --run-id N --kind page --format csv --output pages.csv
```

API：`GET /api/assets?source=scan&run_id=N`；导出：`GET /api/assets/export?source=scan&run_id=N&format=json`。两者支持相同的 `kind`、`context`、`observation`、`q`、`sort`、`order` 参数；列表另支持 `page` 与 `page_size`（最多 200），导出不受分页影响。`observation` 为 `response_observed` 或 `unknown`；`sort` 为 `id`、`url`、`type` 或 `last_seen`，顺序为 `asc`/`desc`。JSON 当前使用 `schema_version: "1.3"`，新增字段保持兼容。

计数基于实际记录：scan 为该任务的扫描资产数，inventory 为页面数加接口数；不跨身份或任务去重，不能解释为独立业务资产数量。相同脱敏 URL 的不同记录不会合并。运行中的计数可能变化；JSON 附来源任务、覆盖说明和计数说明。

“已有响应”只表示记录了 HTTP 响应，401/403 也保留；缺少状态码时显示未知，不判为无效或安全。旧 inventory 缺少可靠完整性及逐项证据关联时保留未知。来源详情展示原记录 ID 和已关联的扫描请求/证据 ID；原始发现链未记录时不补造。身份标签不表示当前会话可用或实际角色已经验证。

浏览、筛选与导出只读取数据库，不访问目标或会话材料。输出 URL 的查询值脱敏，不导出请求体、Cookie、凭据引用或秘密存储路径；CSV 对公式前缀转义。CLI 导出拒绝覆盖已有文件。现有 `garden inventory export` 保持原有格式；新清单请使用 `garden assets export`。

## v0.5.0：多来源发现，汇入同一资产清单

默认继续使用 HTTP 采集；Web 提交页的「发现选项」和 CLI/API 可以显式开启更多来源。

| 模块 | 作用与边界 |
| --- | --- |
| HTML 发现 | 解析链接、内嵌页面、资源引用和表单 action；表单仅作为候选，不提交。 |
| Sitemap | 开启后读取同源 `/sitemap.xml` 或指定地址，支持有界索引递归；跨站索引不访问。 |
| 匿名浏览器 | 执行页面脚本，记录动态请求并发现渲染后的链接；仅允许 GET/HEAD，不加载身份、不点击按钮、不提交表单。 |
| Hash 路由 | 区分 `#/users` 等前端路由视图，保留脱敏路由；视图本身不伪造 HTTP 状态码，使用 `hash-v1` 归并。 |
| JS 线索 | 从已取得的脚本文本提取静态 fetch/XHR/axios 地址；复杂表达式不猜测，线索仅列为候选。 |
| 清单导入 | 接受 UTF-8 URL 列表或 OpenAPI 3 JSON；URL 按同源及预算规则访问，OpenAPI 操作仅作为候选。 |

```bash
# HTTP + 地图 + 已采集脚本中的静态线索
garden scan https://authorized.example --sitemap --js

# 显式使用匿名浏览器，限制页面、资源和请求预算
garden scan https://authorized.example --collection-mode browser \
  --max-pages 20 --max-resources 80 --max-browser-requests 150 --render-wait-ms 1000

# 导入文件；原文不进入公开输出
garden scan https://authorized.example --seed-file urls.txt --seed-format urls
garden scan https://authorized.example --seed-file openapi.json --seed-format openapi

# 按来源查看已有响应或未请求候选
garden assets list --source scan --run-id N --source-kind url_import
garden assets list --source scan --run-id N --view candidates --source-kind openapi_import
```

资产页可按「发现来源」筛选，记录详情、JSON/CSV 保留来源链和候选原因。CLI、详情页和 Markdown 报告展示发现数、新候选、重复、请求尝试、响应观察与跳过数；请求归因于首次入队来源，统计不是站点发现率，也不能证明发现了全部资产。任务执行结束与覆盖完整性仍分别展示。来源列表最多保留 20 条，截断后的精确总量为未知。

候选默认上限 2000，最大 10000；地图默认最多 10 份、递归深度 2；浏览器默认最多 300 次请求、每页等待 1000 毫秒。输入最多 1 MiB、1000 个条目；只支持 URL 文本和 OpenAPI 3 JSON，不支持 YAML、外部 `$ref`、压缩地图或 JS 执行求值。预算耗尽和来源读取失败会保留已知结果及不完整说明。新的浏览器、地图、JS 和导入选项当前用于匿名扫描；已有认证采集流程继续保留。

导入原文和地图地址保存在受保护存储，公开结果隐藏查询参数值。预览临时保留输入 15 分钟，返回修改无需回填原文；沿用历史任务配置时需重新提供输入。浏览器会执行站点自身脚本，请只扫描你已获授权的目标。

本地验收示例：入口包含一个 `/next` 链接和一个 POST `/submit` 表单，再导入一个带查询参数的 `/seed` URL。真实浏览器提交后得到 **3 条已有响应、1 条未请求表单候选**；按 `url_import` 筛选与导出均为 **1 条记录**。报告、清单及预览均不包含导入的查询值。1280px 桌面与 390px 窄屏、启用与禁用 JavaScript 的提交流程均有回归测试。

**升级**：执行 `garden db upgrade` 应用迁移 `0007`，新增可空的私有输入引用和 inventory 来源字段；历史记录来源保持未知。当前资产导出 schema 为 `1.3`，保留原有字段。降级会移除新增元数据，应先备份数据库。

## v0.4.2：有效性线索与候选分离

资产清单新增“有效性线索”筛选与说明：疑似登录页面、登录页回退、软 404、统一响应、统一错误响应和已观察重定向别名。每项保留原因、相关观察与已有请求 ID；原始记录和归并组都能查看。CLI、Web、JSON/CSV 使用同一投影，JSON schema 为 `1.2`，有效性规则为 `passive-v1`。

```bash
garden assets list --source scan --run-id 1 --validity suspected_soft_404
garden assets list --source scan --run-id 1 --view candidates
garden assets export --source scan --run-id 1 --view candidates --format json --output candidates.json
```

API 支持 `view=records|grouped|candidates` 和 `validity=login_page|suspected_login_fallback|suspected_soft_404|uniform_response|uniform_error_response|redirect_alias|none`。`none` 仅表示未命中线索，不代表有效。

- **候选（未请求）**：单独展示 quick scan 正常完成时保存的未请求队列，不计入原始记录或归并资产总量。历史任务、异常中止和 inventory 未提供候选总量时显示“未提供”，不是零。
- **任务已有响应记录**：基于整个来源任务计算，不随视图或筛选改变；401/403 仍属于已有响应，并额外标注受限。`candidate_count` 单独表达已知候选总量，`validity_counts` 表达原始记录的任务级统计。
- **线索不是确认**：所有记录的 `business_validity` 均保持 `unconfirmed`。HTTP 200、未命中线索或扫描结束，都不能证明业务资产有效或覆盖完整。
- **被动分析**：只使用现有采集结果，不增加探测请求、不读取受保护材料。响应对比仅限同站点和同采集身份；截断正文或仅有预览时不生成内容对比特征。登录回退、软 404 与统一响应属于启发式线索，可能误报；材料不足时明确显示“判断材料不足”。

**升级**：执行 `garden db upgrade` 应用新增迁移 `0006`，为扫描候选和 inventory 响应特征增加可空 JSON 字段。旧记录保留未知值，不回填猜测结论。降级到 `0005` 会移除这些新增元数据字段，应先备份数据库。

## v0.4.1：可信归并

在资产清单的“清单视图”中选择“归并资产”，或使用 `view=grouped` / `--view grouped`。默认仍是 v0.4.0 的原始记录视图。

```bash
garden assets list --source scan --run-id N --view grouped
garden assets list --source scan --run-id N --view grouped --context user --json
garden assets export --source scan --run-id N --view grouped --format json --output groups.json
garden assets export --source inventory --run-id N --view grouped --format csv --output groups.csv
```

- **归并资产**是同一任务内的路由族：规则 `route-v1` 包含站点（协议、主机、端口）、方法、原始资源类型、精确路径和查询参数名（保留重复次数）。不同站点、GET/POST、末尾斜杠和 `%2F` 不会混为一项。默认端口、主机大小写沿用 URL 规范化。不能据此声称业务等价或资产有效。
- **请求变体**：v0.4.1 新捕获请求采用 `v2` 私有指纹，按同一采集身份下的方法、完整 URL、请求体和请求头区分。参数值不同但响应相同也会保留。响应状态或稳定内容不同的请求记录分别保存，并可从变体追溯请求 ID；完全相同的捕获仍会去重。
- **观察记录**：保留原始资产记录、身份、覆盖状态及证据关联；组详情另外展示新请求的响应状态与内容类型。同一组同时观察到 200/403 时，摘要保留两者。观察记录数指已持久化的资产记录数，不是所有网络请求次数。
- 新 inventory 接口从已捕获请求 URL 保留参数名及重复次数，隐藏精确值；旧记录不会被猜测性回填。
- **历史限制**：未保存版本化指纹的记录（含 quick scan 与 legacy inventory）不能还原精确请求变体数，显示未知，并保留可追溯的记录。已被旧采集器丢弃的参数差异不能从脱敏 URL 还原。未知不等于零，也不等于没有差异。

API 示例：`GET /api/assets?source=scan&run_id=N&view=grouped`。归并 JSON 当前使用 schema `1.3`，包含 `rule_version`、`observations`、`variants` 和 `matched_observation_count`；CSV 的嵌套字段保存为 JSON 单元格。导出包含全部匹配组及其观察，不受分页影响。筛选先作用于原始观察，组内只包含匹配观察；身份筛选不会伪造其他身份的缺席。

归并 ID 仅在来源任务和规则版本范围内使用，不是跨任务项目资产库主键。普通清单、JSON/CSV 不输出请求指纹、参数精确值、请求体或受保护材料路径，也不会读取受保护材料。

**升级**：本版新增数据库迁移 `0005`，修正扫描资产的方法唯一约束及 inventory 接口的站点隔离，保留旧 ID、请求和证据关联。已有安装按原有流程执行 `garden db upgrade`，可先用只读 `garden doctor` 检查版本。若已写入旧约束无法表示的多方法/跨站点记录，降级会明确拒绝，不删除记录来凑旧格式。

## 如何判断结果

CLI、扫描详情页和 Markdown 报告使用相同计数口径，例如 **2 类关注项，6 条原始观察**。原始观察保留独立证据；按已有规则归并的类别用于阅读，不等同于确认漏洞数量。覆盖矩阵的分类计数单独展示。

CLI、扫描详情页和新生成的 Markdown 报告统一在详细信息之前展示「主要发现、覆盖限制、下一步」结果摘要；详情页另保留执行状态。运行中的观察标为暂定；失败、覆盖不完整或完整性未知时，零关注项不解释为没有问题。已知诊断提供去重后的具体建议，未知原因引导查看诊断和执行阶段，不推测修复方法。诊断直接可见；已结束任务的执行阶段和原始报告默认折叠，可按需展开，报告下载入口始终保留。运行中的任务保持阶段展开并自动刷新。

**执行进度 100% 不代表覆盖完整。** 请同时查看任务状态和覆盖说明：

- 正常普通扫描：本次范围内采集完成，仅包含匿名上下文。
- 正常认证覆盖：本次范围内匿名、普通用户、管理员三上下文采集完成。
- 达到采集限制、请求失败或缺少身份：提示覆盖不完整，并保留诊断和已有结果。
- 尚在执行或旧响应缺少完整性字段：提示尚未确定或未知，不默认显示为完整。

“本次范围内”受入口、同源边界与采集预算约束；即使完整采集且没有关注项，也不能据此判断整个目标安全。`absent` 表示本次完整上下文内未观察到，`unknown` 表示上下文不完整、无法判断。

认证覆盖在生成最终报告前确定完整性，保证新生成报告与最终评估 API 一致。历史报告不会自动重写。API 保留原始 `finding_count`，增加可选的 `finding_group_count`；普通扫描响应也提供原有运行记录的 `completeness`，其中 `legacy_single_context` 表示普通扫描的兼容模式，需结合任务状态判断采集情况。旧响应缺少字段时保持可读的未知状态。

## 覆盖缺口解释

结果页、CLI 和新生成的 Markdown 报告使用同一套解释。例如，匿名扫描显示「页面数上限（2）；未请求 URL：3」，附最多 3 条脱敏样例和对应参数建议。

- 新匿名扫描分别记录页面数、资源数、发现深度限制留下的未请求 URL 数量；按 URL 去重，每个 URL 只归入一个原因。统计只包含本次已经发现的同源候选，无法估算尚未发现的页面。
- 已尝试但失败的请求、采集总超时、跨源跳转拦截与预算缺口分开说明。无法可靠确定数量时显示「数量未知」，不会用重试次数、样例数或旧告警文案推算。
- 认证覆盖分别解释匿名、普通用户和管理员上下文未完成的原因。未观察到的内容仍为 `unknown`；本轮不提供认证浏览器采集的精确未请求计数。
- 没有缺口条目不代表整个站点已覆盖；执行进度 100% 也不代表覆盖完整。

需要补充采集时，可沿用匿名任务配置，调整对应预算后重新预览。建议不会自动扩大范围或提交新任务；跨源目标仍须确认授权。历史记录缺少结构化明细时保留未知，已有报告不会自动重写。API 增加可选的 `coverage_gaps`；旧响应缺少字段时为 `null`，不是零缺口。

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
