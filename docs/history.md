# Garden 版本沿革

本文归档自 2026-11-14 整理前的 README。下列“本轮”、版本号、迁移和测试数量均属于对应历史版本，不代表当前安装状态。当前使用说明见[README](../README.md)，v1.0.1—v2.0.0 的范围和验收见[版本索引](versions/README.md)。

## v1.7.0：macOS App Beta

本轮本地 Beta 为 macOS 26.0+ / Apple Silicon 桌面入口：Tauri 2 窗口捆绑 Python 3.12 与匹配 Chromium，沿用现有 WebUI 和用户数据目录。CLI、浏览器与 App 通过带本地会话的实例握手共享引擎；首次使用明确选择目录、创建或备份升级，退出时区分后台继续与停止收尾。

更新仅由用户触发并确认，使用官方 updater 验证包签名与绑定版本；安装前备份，启动失败回退程序，数据恢复另行显式选择空目录。默认不请求通知权限，也不访问更新源。当前为本地 Beta，正式签名、公证和发布未执行。[构建与使用](../desktop/README.md) · [实施记录](versions/v1.7.0.md)。

## v1.6.0：持续更新与本地数据管理

项目「时效与周期复查」显示最后观察、身份、范围、已保存预算和现有比较分类。超过项目设置的天数（默认 7 天）标为“较旧”，仅是时间提示；未再次观察不表示删除，人工决定只在实际观察属性变化时提示复核。增量预览优先处理缺口与较旧观察，并显示旧证据和本轮预算。

周期计划为固定 1—168 小时（默认 24），绑定一个入口、明确身份、最终参数和每次预算。默认关闭，预览后勾选允许自动请求才能开启；同项目有活动任务则跳过，停机错过不补发，入口/身份变化或身份失效时暂停，归档后恢复可见性也不自动恢复发送。只在现有本地服务运行时执行，不安装系统启动项。

「数据管理」展示占用，提供私有备份、隔离恢复、清理预览与确认。备份先确认没有采集/登录/导出写入，固定 SQLite 与引用文件；目录 0700、文件 0600，可能含会话秘密，不能作为公开交接包。恢复只写入选定空目录并暂停全部计划，完成校验后显示独立 GARDEN_HOME 切换命令，当前服务不切换。环境变量/系统凭据库秘密需另行配置，已有本地引用缺失时明确拒绝生成可恢复备份。

清理仅涉及过期导出、无引用临时材料和明确选定归档项目的生成产物，保留资产历史、备注、复核与引用证据。预览后引用/文件变化会要求重新预览。

```sh
garden projects freshness 1
garden projects schedules 1
garden storage usage
garden storage backup /absolute/path/to/empty-private-backup
garden storage restore /absolute/path/to/private-backup /absolute/path/to/empty-home
garden storage cleanup --retention-days 30  # 只预览，--execute 才显示确认问题
```

周期计划 API：`POST /api/projects/{id}/schedules/preview` 提交 `{"request":{"entry_id":1,"start_at":"2026-11-14T09:00:00+08:00"}}`；随后向 `/api/projects/{id}/schedules` 提交返回的 token 和显式 `enable: true`。迁移 `0016` 仅新增项目新鲜度设置和周期配置，不自动升级日用数据库。[本版实施记录](versions/v1.6.0.md) · 尚未正式发布。

## v1.5.0：报告与资产交付

从项目「资产交付」选择当前筛选，以概要、资产、证据三层查看同一份数据。报告分别显示主体数、身份观察数、人工复核和覆盖缺口；公共术语集中解释，人工保留不表示业务有效。旧报告文件保持原样。

「生成完整交付文件」固定查询、项目修订和格式，按 SQL 分块读取脱敏记录，写成快照后释放数据库，再生成文件。项目修订已变化时要求重新提交；取消、失败和重启中断均不提供半成品，也不自动重做。「已生成」表示 Garden 的文件就绪；点击后仅显示「已发起下载」，保存位置由浏览器和用户确认。

保留 delivery-v1、原机器 CSV 和旧导出入口，新增可读 CSV：资产 ID、URL、方法、类型、状态码、身份、最近观察、复核状态、来源引用。可读 CSV 为 UTF-8 BOM，沿用公式防护；主体视图按匹配身份观察独立输出，同一主体可占多行。

离线 HTML 内含样式；PDF 用同一 HTML 和 Chromium 生成，需可用浏览器及中文字体。ZIP 包含 JSON、CSV、报告、来源索引、schema、字段说明及范围/身份/已保存预算/版本信息。文件只含脱敏历史与公开引用，不包含原始正文、截图、Cookie 或私有会话；历史未保存的预算为未知。Chromium 不可用只影响 PDF。

```sh
garden projects deliver 1 --format readable_csv --output assets-readable.csv
garden projects deliver 1 --format zip --output delivery.zip
garden projects deliver 1 --format pdf --output report.pdf
```

API：`POST /api/projects/{id}/exports` 提交 `{"format":"zip","query":{"collection":"keep"}}`；使用返回的 ID 查询 `/api/exports/{id}`、取消 `/api/exports/{id}/cancel` 或下载就绪文件。新增迁移 `0015` 只存导出配置、修订、状态和受控产物引用；不自动升级日用数据库。实施证据见 [v1.5.0 记录](versions/v1.5.0.md)，本版尚未正式发布。

## v1.4.0：身份采集与多入口

身份页将登录、新上下文恢复验证、采集与补采连续展示，并保留项目入口绑定、最近验证时间和失败步骤。新状态未验证成功前，旧有效会话继续保留；Cookie 文件存在不等于可用。

显式选择「人工浏览」后，独立 Chromium 只记录你与浏览器实际产生的目标请求，允许记录用户 POST；Garden 不会自动点击、提交、刷新或重放。可先在受控登录窗口手动完成验证码/SSO，关闭原窗口并恢复验证后再开始记录；额外登录 origin 只在人工登录阶段生效。控制页持续显示记录状态和停止入口，登录、验证、重定向也占用请求配额。停止或额度耗尽保留已有观察并标记覆盖不完整；请求来源为 manual，禁止自动重放，POST 不进入自动队列或补采。

从项目创建多入口/身份计划，预览全部绑定和总预算，按入口顺序确定配额并可调整后重新预览。每个入口/身份沿用独立的单 origin 子任务；不默认开启可选持久队列，不借用其他子任务剩余预算。结果汇入原项目，跨 origin 链接只保留候选提示，须另加入口后创建新计划。

新增迁移 `0014`：计划仅存子任务引用、身份 ID 与预算配置，不复制秘密材料；旧请求的新增来源字段为未知。实现与合成浏览器证据见 [v1.4.0 记录](versions/v1.4.0.md)。未验证通用厂商 SSO，未自动升级日用数据库或发布。

## v1.3.0：资产工作台与 WebUI

从「项目」打开工作台，查看最近任务、资产分类、保存的身份状态、覆盖缺口和需复核变化。导航统一为项目、任务、资产、身份、报告、设置；页面样式和 HTMX 使用包内固定资源，浏览时不依赖外网 CDN。

项目清单提供核心页面/API、全部资产、候选、人工保留、已忽略和待复核筛选，默认每页 50 条。路径模板仅按原有数字/日期路径族、Origin、方法和参数名聚合展示；展开仍访问原来的资产 ID、来源与身份观察。模板中的状态码是历史集合，不是当前在线状态。

勾选明确的资产后可以批量保留、忽略或恢复未复核，填写理由并记录项目审计；资产详情中可撤销最近复核。再次入库时，已有比较机制发现观察属性变化，会保留人工选择并提示需复核。旧标签和备注保留，不能自动推断为正式复核；人工保留也不证明业务有效。

筛选条件可以按名称保存在项目中；分页和列选择不改变匹配总数，列偏好只存在当前浏览器。模板布局是展示投影，导出仍输出筛选后的实例。新增迁移 `0013` 仅扩展人工注释及项目保存视图，不改原始观察和旧报告。CSS 重建说明见 [离线资源](../app/static/vendor/README.md)，验收与限制见 [v1.3.0 记录](versions/v1.3.0.md)。

## v1.2.0：有效发现与预算调度

Web 提交前可选择 **快速**（原 HTTP 默认）、**平衡**（路径多样性 + HTML / JS / Sitemap）、**浏览器深入**（明确混合模式）。高级参数优先，预览显示最终参数与预算，服务端保存同一快照。旧请求缺少这些选项时保持原义。

```bash
# 仅扫描已授权目标；--preset 同样可用于 API options.preset
garden scan http://127.0.0.1:8080/ --preset balanced
garden scan http://127.0.0.1:8080/ --preset deep --max-requests 50
# 也可显式 --collection-mode hybrid 或 --discovery-strategy diversity
```

多样性调度优先新路径族，再按页面、Sitemap、JS、导入轮转；数字、日期与相同参数名组合按 `max_variants` 有界采样，精确 URL、方法、origin 和身份仍独立。普通扫描无需创建持久队列；持久采集继续用 `durable.max_variants`。候选保留具体未请求原因，扩大预算后可用原预览/确认流程补采，原记录不被改写。JS/OpenAPI 声明继续只作候选，复杂表达式不猜测、不执行。

混合模式仅面向匿名单入口，自动采用持久预算：浏览器预留总请求数的 30%（向下取整，不超过 `max_browser_requests`），HTTP 使用其余额度；无 Hash 路由线索、且不满足“入口无普通同源可访链接但有脚本资源”时，不启动浏览器并归还额度。两阶段共用总截止时间，重定向、重试和浏览器子请求均计费；不自动点击业务按钮。预留为零或 Chromium 不可用时明确提示浏览器缺口并保留 HTTP 结果。Web、CLI 和报告使用同一贡献/候选原因摘要，新增观察不等于有效资产。

固定 50 次预算的合成语料比较和边界见[本版实施记录](versions/v1.2.0.md)。无新增依赖或数据库迁移。

## v1.1.0：扫描性能与执行反馈

HTTP 连接在单个任务内复用，按实际代理和上下文隔离，退出时关闭；匿名请求仍不继承响应 Cookie。持久 HTTP 队列的 `origin_concurrency` 默认为 1，支持 1—4 个有界网络 worker；请求、重定向、重试继续共用 admission、暂停和预算约束。没有持久选项的扫描保持串行。

新任务页通过 `GET /api/scans/{id}/events` 接收脱敏状态快照，只更新阶段、实际计数、等待原因与耗时，保留滚动和折叠状态。断线后使用原状态 API 每 4 秒查询，终态显示最终报告入口；不整页刷新、不按时间虚增进度。旧任务未记录的实时字段仍为未知。

统计快照最多每秒更新一次，终态强制刷新；观察和证据仍按原事务保存。请求、解析、入队、证据提交及快照计时保存在原队列 telemetry 中。当前是单机 SQLite 有界执行，不是分布式调度。具体性能数字和环境见[本版实施记录](versions/v1.1.0.md)。无新增依赖或数据库迁移。

## v1.0.1：实际使用问题收口

安装包元数据是正式运行的版本来源，源码运行读取 `pyproject.toml`；旧 `GARDEN_PROJECT_VERSION` 不再覆盖版本。`garden doctor` 只读展示加载位置、命令入口、数据目录、代理依赖与结构检查；不会连接扫描目标或修复数据库。旧服务没有版本记录时显示未知，更新安装不会自动重启现有服务。

```bash
garden --version
garden doctor
# 仅在确认目标授权后显式触发：一次 GET，不重试、不跟随跳转、不创建扫描
garden connection-test http://127.0.0.1:8080/ --json
# 显式升级；已有 SQLite 数据库修改前自动生成同目录 .pre-upgrade-*.bak
garden db upgrade
```

HTTPX 安装包含 SOCKS 支持，扫描仅使用显式 `GARDEN_SCAN_PROXY_URL` / `GARDEN_SCAN_NO_PROXY`，不继承 shell 代理。迁移 `0012` 仅修复已知的 `scan_contexts.temporary_secret_ref` 缺列/唯一约束漂移，兼容已经手工补列的旧库，不自动清洗重复值；重复值导致迁移失败时保留备份供人工核查。

来源详情在独立有界面板内展示，支持键盘打开和 Esc 关闭；采集中显示实际请求、响应、线索、待处理量与耗时。历史缺失计数显示未知；执行结束仍不等于覆盖完整。[本版实施与验收记录](versions/v1.0.1.md)。

## v1.0.0：可重复的质量验收

提供六维合成质量基线：发现、归并、可信度、数据完整性、持续更新和边界控制。报告记录实际执行结果、提交与环境，列出缺失、跳过及失败检查，并给出固定语料的发现召回、候选确认与误合并计数。

```bash
# 从源码仓库、安装开发依赖和 Chromium 后运行
python scripts/quality_baseline.py
# 发布准备：全量只运行一次，同时提取质量门禁
python scripts/quality_baseline.py --full
```

默认输出 `.verification/quality/quality.md` 与 `quality.json`。CI 的两个 Python 任务也保存同类报告。**合成测试通过不代表真实站点发现率 100%，也不代表已发布。** 该历史版本建立质量基线；当前候选的 CI 状态见 v2.0.0 实施记录。该片没有新增依赖、数据库迁移或扫描动作。[验收口径与发布检查](quality/README.md)。

## v0.9.0：资产交付与集成

项目清单可将当前筛选直接带入「资产交付」：分别下载主体、请求变体、身份观察、候选和关系；项目比较页可交付变更清单。版本化 JSON Schema 与修订绑定分页便于系统消费，JSON/CSV 导出全部匹配结果，Markdown/HTML 提供概要与证据入口。

```bash
garden projects deliver 1 --dataset observations --format json
garden projects deliver 1 --dataset variants --format csv
garden projects deliver 1 --dataset changes --before-run-id 2 --after-run-id 3
garden projects deliver 1 --dataset observations --format openapi
```

OpenAPI 是已观察接口的推断草稿，不猜测鉴权、正文或响应 schema。旧快照未记录的请求变体明确为未知，不读取私有材料补猜。原有导出接口保持兼容，无新增数据库迁移；该历史版本的迁移为 `0011`。[交付契约、分页示例与限制](asset-delivery-v1.md)。尚未发布。

## v0.8.0：持久队列与增量补采

扫描可以显式启用持久队列。请求预算、已观察工作、未完成原因和发送尝试一起保存；暂停后停止新增请求，在途请求收尾。进程重启本身不会访问目标，补采必须先预览，再确认创建关联的新任务，原任务和报告保留。

```bash
garden db upgrade
garden scan https://example.test/ --durable --max-requests 100 --min-interval-ms 500
# 使用实际返回的任务 ID 和队列 ID；两者并不总相同
garden queue show 12
garden queue work 4
garden queue pause 4
garden queue resume 4 --preview-only
garden queue resume 4
garden queue export 4 --format markdown
```

Web 任务页可进入「持久采集队列」，查看预算、未覆盖原因和恢复预览。预览展示原预算、新轮预算与不可恢复材料；「仅已知工作」不扩展发现范围，「继续同源发现」仍受原范围和新预算约束。匿名 HTTP／浏览器与独立身份采集可使用；固定三身份覆盖流程不支持此选项。

观察效率、身份验证请求、重试、等待时间与项目新增主体／观察使用统一口径。旧记录没有指标时显示未知；未关联项目显示不适用。**已知队列覆盖完成不代表全站覆盖，已观察也不等于业务有效资产。** 当前实现为单机 SQLite 有界调度，不是分布式任务系统。

数据库迁移至 `0011`；升级前备份数据库及受保护请求材料，有持久历史时拒绝有损降级。[操作与实测记录](demo-v08-durable-collection.md) · [规模实验及限制](benchmarks/v08-collection.md)。本地验收完成，版本 0.8.0，尚未发布。完整回归 910 项通过；Linux 未验证。

## v0.7.0：跨任务项目资产库

v0.7.0 引入项目资产库。升级数据库至迁移 `0009` 后，从 Web 导航进入「项目资产库」：创建项目、绑定已有 Target 与精确入口，再通过原扫描／身份预览流程采集，或预览导入已有终态任务。

项目清单分别统计路由主体、历史观察、未请求候选和已保存关系。同一路由保留不同任务与身份的观察，人工合并／拆分只改变展示归属，并可按依赖顺序撤销。比较仅针对显式选择的两次项目运行；不完整覆盖、身份不明或配置变化返回未知，“未再次观察到”不等于删除。项目成员资格不扩大扫描许可。

```bash
garden projects create "站点资产"
# 将返回的项目 ID、已有 Target ID 和入口 URL 代入
garden projects entry-add 1 --target-id 1 --url https://example.test/ --name 主站
garden projects import 1 --entry-id 1 --source scan --run-id 12
garden projects assets 1 --json
garden projects --help
```

历史导入先预览、再确认；JSON/CSV 导出全部筛选结果，不限于当前页。归档保留读取，停止入库和整理。有项目数据时拒绝有损降级。[真实浏览器演示与验收边界](demo-v07-project-assets.md)。

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

[真实本地 v0.6 演示与验证记录](demo-v06-identities.md)

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
