# v0.8.0 实施分片与 TDD 验收计划

设计：[增量采集与持久调度](../specs/2026-10-11-v08-incremental-scanning.md)。
基线：`8c7de9bcac3f0c7b9ce0dfcd107a6725607c1896`，隔离分支 `codex/incremental-scanning-v08`。
状态（2026-10-14）：TDD 公开接口已确认；S01–S06 已有本地检查点，S07/S08 仍在实施，S09 验收未完成。版本仍为 0.7.0。

## 执行约定

- 一个可观察失败场景 → 最小实现 → 针对性通过，垂直推进；不先铺全部单测，不人为把已通过边界改红。
- 通过公开服务/适配器验证。外部 HTTP、时间、进程终止可控；不 mock 私有内部调用顺序。真实 SQLite 与合成站点优先。
- 新表/请求材料/调度细节保持深模块；适配器不能重复实现规则。SQLite 写事务中不运行网络或等待；Session 不跨线程。
- 每片记真实命令、SHA、退出码、摘要、未验证项。冻结实现后跑完整串行浏览器回归，不在全量进行中编辑产品代码。
- 当片通过后可本地提交；本次不自动推送/创建 PR/打 tag/发布。未满足全部 AC 不标整个版本完成，不只做第一片就自行结束。
- 不恢复 Superpowers；此目录为普通版本文档。只读最终评审可按 code-review 技能派发两名评审者；不派发实现代理。

## S01：持久契约、迁移与私有请求存储

覆盖 AC01/03/09。建议文件：`app/models/collection_queue.py`、`app/schemas/collection_queue.py`、`app/services/collection_storage.py`、`migrations/versions/0010_collection_queue.py`；注册模型，更新只读 doctor 与迁移断言。

1. RED：从 0009 带原 ScanRun/Project 数据库升级，公开队列服务创建并读回；旧数据、未知指标不变。
2. 最小实现：队列/工作/尝试/租约/门及恢复唯一约束；公开 DTO 显式排除保护引用/token/digest。
3. RED：原始查询、Cookie 和哈希路由敏感值不出现在 API/日志/导出；私有材料路径逃逸、symlink、内容变更、损坏或文件大小超限拒绝。
4. 最小保护存储：0600 文件完整写入后发布引用；短事务回滚不发布损坏内容，孤儿文件处理有记录。
5. RED：有历史不允许降级，无数据可回退；doctor subprocess 无 DB/file 写入。
6. 验证：新契约、迁移、doctor 和旧身份/项目契约测试。保持运行版本号 0.7.0。

## S02：持久入队、claim、attempt 与暂停

覆盖 AC02/03。建议文件：`app/services/collection_queue.py`、`collection_transactions.py`、`collection_leases.py`；测试 `tests/test_collection_queue.py`。

1. RED：含不同参数值/方法/identity 的工作不误合并；同一工作多来源、重复提交仅一条可执行记录，来源保留。
2. 通过真实独立 SQLite 会话争用 claim，只有一方获取；CAS 失败可读出重试原因，非锁错误不吞。
3. RED：checkpoint 在响应/证据/新候选/工作完成之间异常时全回滚；重试同 attempt 不增重复观察。
4. RED：暂停后不再 claim，已发送记录可收敛；过期 token 不能完成新 worker 的尝试。
5. 限定状态机：pending/leased/deferred/observed/failed/blocked_identity/suppressed/uncertain；任务终态与覆盖分开。
6. 验证所有结果经公开 get/list_work；无测试直写被测实现内部状态，fixture 只建立必要原始对象。

## S03：统一请求准入、计费与 origin/identity 门

覆盖 AC04/09。建议文件：`app/services/collection_admission.py`、`collection_budget.py`；测试 `tests/test_collection_admission.py`。

1. RED：两个任务/身份共享 origin 发送间隔；相同身份串行，不同 origin 不相互阻塞。
2. RED：每次 reserve 都可解释为 sent/cancelled_before_send/possibly_sent；崩溃不退款后漏记预算。
3. RED：请求、候选、序列化证据上限并发预留不超支；取消与 deadline 优先于等待；零分母为 null。
4. 真实线程/可控时钟做准入时序，真实服务计数对照实际发送；不以调用次数 mock 证明网络次数。
5. 明确活动采集 context 并发与浏览器 TCP 并发不是同一指标，输出不混淆。

## S04：HTTP 持久采集与退避

覆盖 AC02/04/07/08/09。建议文件：`scan_pipeline.py`、`scan_network.py`、`discovery_scan.py`、`discovery.py`、`collection_retry.py`、`collection_priority.py`；测试 `tests/test_collection_http.py`。

1. RED：入口、HTML 子链接、Sitemap、资源引用在首次发送前可从持久队列读取；HTTP redirect 每一跳通过范围和预算门。
2. 最小接入公开 ScanApplicationService，原无持久配置流程保持现有行为。减少内部循环重复而非另写不接线的 collector。
3. RED：429/503→Retry-After 秒数/日期；合法未来时间不提前访问，坏值指数退避；冷却跨身份、跨任务和重启保持。
4. 将旧 gateway 重试与调度重试统一计数，禁止乘法重试；连接失败与有响应失败分别保存。
5. RED：合成无限日历/分页/参数组合有界；被抑制工作和原因可查询，真实不同查询值不静默合并。
6. RED：chunked/虚假 Content-Length/大响应不突破 256KiB 保存/解析上限；超证据预算仍保留截断说明。
7. 本地真实 HTTP 集成，确认请求计数与守门记录一致；不扫描外部站点。

## S05：浏览器与身份采集持久接入

覆盖 AC02/04/05/09。建议文件：`browser_discovery.py`、`discovery_browser.py`、`identity_collection.py`、`identity_health.py`、`identity_sessions.py`；测试 `tests/test_collection_browser_e2e.py`。

1. RED：无 JS 和真实 JS 页面/iframe/XHR/fetch/重定向逐实际发送计费；浏览器分配一个身份独立 context。
2. 先持久导航工作，子请求在 CDP 准入时保存 attempt；导航取消/无响应不得标 observed。
3. RED：健康检查消耗相同 origin/identity 预算，捕获的当前 live 状态用于健康验证；收集过程中登录过期停止新业务发送，不冒充匿名已确认。
4. 在健康验证需要单独线程时不共享 collector Session，不在 CDP 回调里持有 DB 写锁等待验证；心跳不得依赖被阻塞的浏览器事件循环。
5. RED：未知长度/编码/过大响应不调用无界 body()；不把未捕获 body 当空 body。
6. 验证旧单/双身份验证码登录、恢复状态、确认/不确定分批、已知补采兼容；对生产浏览器资源消耗不做未经测量的保证。

## S06：预览恢复、真实崩溃与工作转交

覆盖 AC02/03/05/06。建议文件：`app/services/collection_resume.py`、`collection_worker.py`、`scan_application.py`、`identity_recovery.py`、`main.py`；测试 `tests/test_collection_resume.py`、`test_collection_process_recovery.py`。

1. RED：恢复预览无访问目标、无持久写入；绑定 queue revision、Target 精确配置、project entry、profile/session、options、选中 work 集合。
2. RED：同 origin 路径变更、身份重绑定、撤销会话、配置修改或工作版本变化均使确认失效；验证新登录态前不能发送业务请求。
3. 确认新子 ScanRun + 原工作转交同事务、唯一键幂等；项目绑定保持原精确入口，子任务实际起始请求与项目入口证明分开。
4. RED：原报告字节/项目快照不变；新观察独立入库；父未完成历史仍如实存在，不错误提升原轮次 completeness。
5. 本地 subprocess 在“领取后”“已发送未提交”“已提交未退出”分别终止；重启不触网，known_only 恢复后无丢项、不确定发送明确。
6. 验证 worker 心跳/旧租约 fencing、另一个活 worker 不被启动中断逻辑误杀；多服务进程若不支持，持有明确单属主锁并拒绝第二执行器，不能静默双跑。
7. 对仅有公开脱敏地址的旧历史请求显示不可恢复原因，不猜测原始材料。保留旧 v0.6 检查点入口的真实回归。

## S07：项目增量计划与优先级

覆盖 AC05/06/07。建议文件：`app/services/project_incremental.py`、项目接口及 schema；测试 `tests/test_project_incremental.py`。

1. RED：预览可重要入口/新线索/较旧观察排序，重复目标/身份工作去重；老工作可获得执行机会，不饥饿。
2. 只使用原私有请求证明生成可执行复查项；缺材料/超范围/非只读候选明确列出不执行原因。
3. 显式区分已知补采和继续发现：known_only 浏览器不放宽辅助请求；continue_discovery 预览显示会新增发现范围。
4. 计划确认绑定项目/入口修订；归档或重新绑定后拒绝旧确认。顺序和优先级不能越过退避/预算/身份门。
5. 不把本次跳过或缺覆盖的新旧差异解释为删除；v0.7 比较保持保守语义。

## S08：API、CLI、原生 Web 与导出闭环

覆盖 AC10。建议文件：`app/api/routes/collection_queue.py`、`collection_pages.py`、`app/cli/queue.py`、`app/templates/collection/`；扫描/身份/项目适配器只传递共享服务结果。

1. RED：API 预览→确认→暂停→队列筛选分页→恢复，陈旧确认 409、无效请求 400、缺来源 404，错误不回显敏感数据。
2. CLI 同等流程，取消确认不写入、不触网；非交互显式确认，恢复预算在提交前完整显示。
3. Web 任务详情、身份上下文、项目入口可直接进入队列；390×844/1280×900、无 JS 表单可操作。
4. JSON/CSV/Markdown 包含 queue_scope、过滤条件、版本、发送/不确定/未覆盖、transport 分组；零行保留元数据，公式转义，分页导出不漏条。
5. 旧结果显示“未记录”，未关联项目的新增项目主体为不适用；执行结束与覆盖完整分开，100% 只用于已知执行阶段。
6. 新增公开 body 上限/同源检查/只读 no-store；用户流程不暴露 fencing、内部 SQL 或私有文件名。

## S09：真实万级实验、完整验收与文档

覆盖 AC11/12；前八片全部通过后执行。

- 合成万级 URL 队列去重、选择/分页/导出完整性；保留真实请求参数变体，归并不影响网络工作身份。
- 真实本地 HTTP 万级和浏览器万级分别运行，使用显式批准于本地 fixture 的预算；记录耗时、真实尝试、确认观察、峰值 RSS、SQLite/证据体积、等待/冷却。浏览器若资源不足未跑完则保留真实剩余与未验证，不以队列插入或较小样本代替完成。
- 同一合成站点故障→退出→预览→范围/身份重验→补采→项目查询/导出，检查原报告不变与新轮次来源；截图与输出位于忽略目录。
- 新独立 Standards/Spec 只读评审，固定 base→HEAD；先失败再修复实质问题，复审范围/未执行测试明确记载。
- 冻结产品代码，完整 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -o addopts='' -q`；Ruff check/format、git diff --check；完整通过再收尾文档。
- wheel/sdist 包含 0010、队列模板/服务/CLI；新临时 venv 干净安装、0009→0010、版本/help/只读查询、CLI 生命周期。区分安装冒烟与完整 pytest 的依赖环境。
- 全部 AC 核销后设 0.8.0，版本一致性测试先 RED 后 GREEN；版本变更进入冻结后的最终测试，不用旧全量冒充。README/展示页据真实旅程更新，说明性能平台和规模限制。
- 仅提交本次已验证文件，记录最终 SHA。确认主工作区改动未动，停止本条版本；不自行进入 v0.9。

## 2026-10-11 开始前证据

- `git fetch origin`：main 从 1529fe6 前进至 `8c7de9b`，v0.7 PR #37 已合并；新建隔离 worktree `.worktrees/incremental-scanning-v08`。
- 主目录仍为用户原来的脏 main（a8e3b1d）；没有 stash/reset/覆盖。
- 仓库/上级目录未发现适用 AGENTS.md 或 CONTEXT.md；没有现成 v0.8 实施规格，本文根据原路线与当前代码建立，不凭旧版本记忆补造。
- 现有固定响应上限实测代码是 256KiB（`scan_network.MAX_RESPONSE_BYTES` 与浏览器长度守门），设计保留该上限，不擅自放大。
- 仅文档设计，未运行新的产品测试；v0.7 的 826 passed 不作为本轮 v0.8 验证成绩。
- 当前明确阻塞：TDD 公开接口确认已发出、尚未收到回复；未写依赖该确认的新测试或实现。收到确认后从 S01 垂直推进至 S09。


2026-10-11：用户在公开接口确认问题后回复“继续”，按已列接口与验收边界实施；确认门已满足。S01 已开始，其他分片未完成。

### 基础实现检查点（尚非整版完成）

公开服务/保护文件首轮 RED 为缺少 collection schema 模块；实现后发现测试 fixture 的入口缺 `/`，按规范 URL 调整 fixture。租约和计费首轮 RED 为缺少接口；随后 WAL 陈旧租约错误码、变体保留、多来源及同 origin 并发分别先 RED 后修复。

本机 Python 3.10.10：`tests/test_collection_queue.py tests/test_collection_admission.py` 10 passed / 2.60s；此前队列+迁移+doctor 66 passed / 27.03s（存在交集，不相加）。迁移 0010、保护文件校验、查询脱敏、租约 fencing、暂停、未知工作、实际发送预留、持久退避及并发门已实现基础服务，尚待真实采集接线与故障旅程。Ruff check/diff 检查通过。S04 的公开扫描入口用例已 RED（旧执行没有持久配置/队列），本检查点不提交该待实现用例。


### 2026-10-12 S04/S05 接线进展（未完成整版验收）

环境仍为本机 Python 3.10.10、仓库现有虚拟环境与本地 Chromium；基线提交 c57a17d，以下产品变更在工作区，尚未发布。

- HTTP 通过扫描公开入口消费持久队列，重定向/Sitemap/普通请求统一计费。旧比较选项兼容新增 nullable durable 字段，避免旧报告错误降为 unknown。
- 匿名浏览器导航与子请求通过 CDP 准入；Sitemap、重定向和 hash 路由均有真实本地站点测试。成功的 hash/重定向导航还需已捕获底层文档才能完成工作项；预算耗尽保留观察和未覆盖工作，显示 completed_with_warnings。
- 独立身份采集创建分身份队列，健康验证与业务使用同一预算。真实重定向用例发现普通 route 钩子仅计首跳（目标收到 5 次、账本 3 次），改为 CDP 逐跳守门后 5 次均计费。验证线程使用独立 Session，不跨线程共享采集 Session。
- 收尾身份验证预算不足时，队列将未确认批次保留为 uncertain，不因业务响应已到达而宣称覆盖完整。

本次 TDD 命令均以 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -o addopts='' -q` 执行，浏览器使用本地合成 fixture，日志在忽略目录 `.verification/`：

| 场景 | RED | GREEN |
|---|---|---|
| 浏览器 Sitemap 隐藏入口 | s05-sitemap-red.log，隐藏入口未访问 | s05-sitemap-green.log，2 passed / 6.45s |
| 重定向及 hash 完成记录 | s05-route-red.log，coverage_complete=False | s05-route-green.log，8 passed / 21.86s，含旧浏览器回归 |
| 预算停止的结果语义 | s05-budget-red.log，错误显示 failed | s05-budget-green.log，4 passed / 10.70s |
| 身份业务与验证共享预算 | s05-identity-red.log，未建立队列 | s05-identity-green.log，1 passed / 8.10s |
| 验证重定向逐跳计费 | s05-health-redirect-red.log，3 != 5 | s05-health-redirect-green.log，2 passed / 14.41s |
| 身份收尾未确认保守记录 | s05-health-incomplete-red.log，错误显示覆盖完整 | s05-health-incomplete-green.log，3 passed / 21.57s |

以上测试集合有交集，不累计成独立测试总数。S04 既有 targeted 回归为 43 passed / 14.57s（s04-regression-retry.log）。新的集成回归 `s05-integration-regression.log`：143 passed，2 个依赖弃用 warning，258.69s。命令覆盖 collection_queue/admission/http/browser_e2e/identity_e2e、discovery_browser/pipeline/submission、identity_collection/sessions/health/recovery/review_regressions、scan_preview_pages、migrations、doctor。此为针对性回归，不是完整 pytest。

待完成：浏览器退避重试、身份辅助流量失败状态/证据预算细化、租约失效后的最终写入隔离、真正崩溃/新轮恢复、项目增量规划、API/CLI/Web/导出、万级真实采集、完整 pytest 和包安装验收。版本号保持 0.7.0；不把本检查点描述成可发布的 v0.8。


### 2026-10-12 S06 恢复基础与边界修复（仍在实施）

S04/S05 检查点本地提交 `c7f12f4`，未推送。其后 S06 在工作区推进：

- 新增只读恢复预览，绑定 queue revision、原始工作材料、Target 精确地址、身份当前配置/会话与项目入口；默认 known_only，展示脱敏样例和新轮预算。
- 确认原子建立新 ScanRun/context/queue 与旧工作转交记录，重复确认返回同一轮；旧报告不修改。项目补采新轮独立入库，原快照不变。
- known_only 同时限制 HTTP 请求、重定向及浏览器子请求；必要身份验证仍遵守固定验证配置/同源限制并使用同一预算，预览明确显示要重验和计费。
- 源目标在其他 Session 改动后，旧 Session 缓存不能绕过校验；binding 强制刷新 Target/profile。
- 启动不误中断持有效租约的任务；失效租约回收保留 uncertain。旧工作者失去租约后不保存迟到响应，不生成报告，交由启动恢复标记中断。
- 暂停后允许已有响应结算，但队列仍为 paused。浏览器 429/502/503/504 使用持久 deferred 工作与 origin 退避重试，不直接算作 observed。

真实命令前缀同前，以下均为本机 Python 3.10.10 新跑：

| 日志 | 验证结果 |
|---|---|
| s06-preview-red / green | 缺新服务接口 → 只读/脱敏/精确 Target 绑定 1 passed / 0.47s |
| s06-linked-red / green | 缺新轮创建接口 → 原报告不变、已知补采、幂等 2 passed / 1.01s |
| s06-project-red / green | 先修正 fixture 的公开 runs 返回值使用；真实 RED 为仅有 1 个项目轮次，修复关联后含队列/HTTP 12 passed / 4.44s |
| s06-stale-red / green | 缓存 Target 漏检 → 刷新绑定后 11 passed / 3.92s |
| s06-startup-red / green | 误中断活 worker → live lease 保护后 12 passed / 4.19s |
| s06-identity-red / green | 身份补采未通过必要重验 → 3 passed / 27.73s，含真实 Chromium |
| s06-process-green | 真实子进程在 claimed/sent/acknowledged 后 SIGKILL，3 passed / 3.37s；重启无新增请求。过期时钟通过公共 clock seam 前移 121s，未真实等待租约时长。这是队列/准入协议的实进程证据，acknowledged 分支不是完整扫描证据事务故障实验的替代品 |
| s05-browser-retry-red / green | 429 仅发送一次 → 持久重试后 5 passed / 13.45s |
| s06-pause-red / green | 收尾误变 finished → paused 保留，1 passed / 0.37s |
| s06-browser-scope-red / green | 新 XHR 中断全部补采 → 拦截后继续已选工作，含身份回归 9 passed / 42.76s |
| s06-fence-red / green | 过期 worker 仍生成完成结果 → 拒绝收尾写入，8 passed / 2.96s |

进程实验初次 sent/acknowledged 失败为子进程继承本机 SOCKS 代理但未安装 socksio；合成 fixture 明确 trust_env=False 后运行成功，无新增依赖。一次编辑/浏览器测试调用因自动审批服务额度不足未执行，用户继续后按相同审批方式成功重试，未绕过审批。

尚未核销 S06：单属主执行器/启动-派发竞态、完整扫描证据事务各崩溃点、派发失败恢复和抑制项重选等仍需落实；当前组合回归 s06-regression 正在执行。S07 项目增量计划及 S08/S09 仍未实施完成。未调高版本号，不发布。


#### S06 后续验证与本地检查点

- `s06-regression.log`：110 passed / 235.48s（恢复、崩溃、浏览器、旧身份、迁移、doctor）。
- 增加本地 SQLite 执行器 flock 所有权，同一进程多个服务引用共享；第二进程可查询，但不能开始持久执行或启动恢复。构造/只读预览不建锁文件。真实进程共享/释放测试 `s06-owner-green.log` 1 passed / 1.11s；入口组合 `s06-owner-entry-green.log` 10 passed / 5.09s。入口测试首轮 fixture 漏初始化数据库，补齐后验证；不把该 fixture 错误描述成产品缺陷。
- 派发失败子轮保留为 failed/dispatch_failed，工作仍 pending、发送计数为 0，可再次预览。RED 为 dispatcher 异常直接抛出，`s06-dispatch-green.log` 8 passed / 4.36s。
- 显式 work_ids 可选择 suppressed 工作，默认选择仍排除 suppressed；新变体预算在预览显示。`s06-suppressed-green.log` 6 passed / 2.24s。
- 所有权接线的 `s06-owner-regression.log` 为 126 passed、1 failed / 206.04s。唯一失败是旧 stop 替身未实现 shutdown 且断言旧顺序；先停止 Web 进程释放执行权、再处理中断、最后关闭服务是新生命周期要求。更新该测试，并新增真正的 Web 子进程 + `python -m app.cli.main stop`，`s06-stop-green.log` 20 passed / 11.28s。
- 上述为互相重叠的针对性回归，未称完整 pytest。原始报告/快照不可变、known_only XHR 限制、身份复核计费继续通过。当前平台为 macOS；flock 路径尚未在 Linux 实跑，Windows 执行器未验证。
- S06 不宣称全 AC 完成：完整扫描证据事务的全部进程崩溃点、原始材料清理、所有读写范围与极端输入的最终评审仍待 S09；旧协议实进程实验范围如上表所述。
- S07 已开始公开项目预览 seam 的 RED/GREEN：旧观察缺原请求材料明确 blocked；现有持久材料可按 recovery/new_candidate/stale_observation 排序。`s07-preview-green.log` 1 passed / 0.40s；`s07-order-green.log` 2 passed / 0.73s。S07 的确认执行、重要入口、老化和范围变更确认尚未完成，代码不混入 S06 检查点。

### 2026-10-13 S07 执行接线与 S08 多入口（仍在实施）

基于本地 `5e40778`，Python 3.10.10 / macOS。原主工作区保持不动，本节尚不是 v0.8 发布验收。

- 项目增量预览只从持久队列的受保护原始请求生成工作；历史观察缺材料明确 blocked。可选同一项目入口的匿名或一个 profile，默认 known_only；显示继承的扫描限制、新轮预算、身份复核计费和截断说明。
- 确认使用有界、15 分钟内存令牌，绑定参数、项目修订/入口、原工作修订/材料、Target/profile 配置及当前身份会话；新轮复用正常扫描/身份调度和项目入库。重复确认返回同一轮，不重复发送。进程重启后需重新预览。
- 优先重要资产、恢复、新候选、过期观察；每等待 7 天提升一级，最低优先级数字为 0，同级以原工作 ID 排序。原观察不转为“消失”。恢复不放宽现有扫描上限。
- API 增加队列查询/分页/暂停/恢复预览/确认、项目增量预览/确认和导出；POST 沿用同源和 512 KiB 限制，响应 no-store。CLI 新增 queue show/work/pause/resume/export 与 projects incremental，默认明确确认，取消不启动服务或新任务。网页提供原生表单及项目入口链接、中文预算标签和本地无 JS 样式。
- JSON/CSV/Markdown 共用队列投影，分页导出含全部选中项，零行仍保留范围/预算/发送指标；私有引用和查询值不输出。不可表示的远期退避在公开投影为 null 并附标识，内部不缩短退避。
- 暂停测试暴露 HTTP 入队新线索拒绝 paused 导致响应事务回滚；允许 paused 保留候选，发送准入仍拒绝。浏览器发送前 attempt 投影未提交导致网络等待期间持锁、暂停报并发冲突；发送前释放该短事务，collection_paused 按有记录的停止处理。

本次真实命令仍以 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest ... -q -o addopts=''` 执行；较早日志使用仓库默认 addopts，只显示通过点数，不补造耗时。

| 证据日志（忽略目录 `.verification/`） | 本次结果 |
|---|---|
| s07-start-red / green | 缺确认执行接口 → 2 项通过，验证 recovery/new_candidate/stale 顺序与独立项目轮次 |
| s07-binding-red / green | 预览后 Target type 改动仍放行 → 加入当前配置绑定核验，12 项通过 |
| s07-identity-red / green | 缺身份执行接线 → 6 项通过，包含真实 Chromium 的验证→资产→复核，以及幂等确认 |
| s07-aging-red / green | 旧工作始终排在新工作后 → 等待老化，3 项通过；一次编辑误插入计算导致 UnboundLocalError，已修复，不当作独立产品 RED |
| s07-options-red / green | 预览缺继承限制 → 增加 scan_options 与范围说明，3 项通过 |
| s07-regression | 67 passed / 174.72s，2 条依赖 deprecation warning |
| s08-adapters-green | 5 passed / 2.41s（API、CLI、原生表单、导出）。空筛选初次用 uncertain，而 fixture 恰有 1 条 uncertain；改用 suppressed，不将断言错误算产品缺陷 |
| s08-nonfinite-red / green | JSON 对 inf 失败 → 公开 nullable 退避投影，12 passed / 3.22s |
| s08-project-web-green | 3 passed / 1.70s，原生项目增量预览与 API 确认 |
| s08-incremental-cli-green | 1 passed / 0.52s，CLI 项目预览不触网 |
| s08-regression | 89 passed / 44.09s，含项目适配器、迁移、doctor；早于后续暂停修复，不能替代该修复后的全量回归 |
| s08-pause-red / green | HTTP 暂停丢失已收观察 → 14 passed / 6.06s |
| s08-browser-pause-red / green | 浏览器网络等待期间持锁导致暂停冲突 → 7 passed / 20.74s，真实 Chromium |

真实 UI 检查：隔离临时 SQLite + 合成请求 fixture（HTTP 业务请求为 MockTransport，仅 UI 为真实浏览器，不能算万级真实 HTTP 验收）。Playwright CLI 使用已安装 Chromium 1208，`javaScriptEnabled=false`，390×844 原生展开→预览→确认后跳转新任务 #2。1280×900 截图和实际 DOM 宽度均 1280，无横向溢出；宽度命令返回较慢但最终有输出。截图 `output/playwright/v08-queue-mobile.png`、`v08-preview-mobile.png`、`v08-queue-desktop.png`；桌面文本/尺寸 `.verification/s08-browser-desktop.txt`。浏览器及临时服务已关闭。

工具环境：首次 npm 缓存写入 EPERM，改用 `/private/tmp/garden-playwright-cache`，未修改用户缓存权限。CLI 所需 Chromium 1247 未安装，使用项目现有 1208 可执行文件，未安装另一份浏览器。

当前正执行 `.verification/v08-full-pytest-first.log`（首轮完整回归，结果未定，不记为通过）。剩余：S07/S08 竞态与所有入口最终核查、完整指标投影、旧私有材料支持/解释及选择交互；S06 完整扫描事务崩溃实验、存储路径/孤儿文件与极端输入审查；真实万级 HTTP/浏览器、独立双评审、包构建与干净安装、Linux 实证、最终版本/README/展示页。尚未核销全部 AC，版本保持 0.7.0，未推送或创建 PR。

环境补查：2026-10-13 `docker info --format '{{.OSType}} {{.ServerVersion}}'` 和 `docker image ls` 均返回无法连接 `/Users/an/.docker/run/docker.sock`；当前无可用 Docker daemon，Linux 实测未完成。本机另有 `/opt/homebrew/bin/python3.12`，当前记录的测试仍明确为 Garden 支持的 Python 3.10.10，不混用环境结果。

#### 当前 AC 核销差额（2026-10-13，非完成标签）

- AC01：已有迁移/doctor 回归；最终 wheel/sdist/干净安装待验。
- AC02：已有协议进程 SIGKILL 和过期 worker 隔离；完整扫描资产/证据事务各崩溃窗口待验。
- AC03：顺序重复确认已验证；同一增量令牌并发确认需补。人工阅读发现 quick 入口锁内先 validate 后查已创建队列，第二确认可能在项目修订变化后收到 stale；身份入口还需核实唯一约束冲突转幂等响应。
- AC04：HTTP/浏览器暂停保留在途响应已验证；同进程匿名与身份分别持 dispatcher 的全局 worker 上限仍须统一核实。
- AC05/06：现有范围/profile 配置、known_only HTTP/浏览器、身份重验与历史保留回归已通过；确认后执行前身份会话变动、绑定竞态和 source work.not_before 向新轮传递待补。
- AC07：精确请求去重、参数配额与项目老化已有证据；全规模分页/筛选/选择以及明显无效 important_asset_id 的用户反馈仍待核实。
- AC08：已有 HTTP/浏览器响应重试和持久 Retry-After；浏览器传输失败工作重试归类、极端远期值需补全过程证据。
- AC09：已接预算门；身份后处理附加证据字节、并发同 URL 的浏览器响应归属、私有目录祖先符号链接/孤儿清理仍待审查。
- AC10：API/CLI/Web/导出的基本队列指标和无 JS 恢复闭环已验；完整项目新增/身份观察/重复/等待耗时指标、身份详情链接、筛选/显式重选与错误页体验仍待补。
- AC11：真实万级 HTTP 和浏览器尚未执行，不能以现有队列/小规模 fixture 代替。
- AC12：完整 pytest 当前在跑；最终冻结后回归、独立 Spec/Standards 评审、版本一致性与发布准备仍待完成。Docker daemon 不可用，Linux 未验证。


### 2026-10-14 继续实施与回归记录

工作分支 `codex/incremental-scanning-v08`，基于本地 `5e40778cd5c061cec9f47084cf3088cb5735ce9c` 的未提交实现；主工作区保留。仍不推送、建 PR 或发布。

#### 上轮检查的实际结果

- 首次全量 `.verification/v08-full-pytest-first.log`：**871 passed / 1 failed / 2 warnings，744.28s**。唯一失败为 `test_desktop_mobile_identity_journey_without_javascript[True-1]`，预期 completed、实际 completed_with_warnings；尚未定位根因。仅给原断言添加完整公开 assessment 诊断，未放宽断言。首次临时测试目录被后续 pytest 清理，不能声称保留或读到了该失败的数据库。
- 同参数独立复测 `.verification/v08-identity-journey-repro.log`：1 passed / 16.39s；完整身份旅程三轮串行复测 `.verification/v08-identity-journey-loop-{1,2,3}.log`：各 3 passed / 2 warnings，分别 60.28s、60.62s、62.53s。三轮全绿不等于首次失败已修复；第二轮完整回归指定保留 `.verification/full-second-temp`。
- 增量同令牌并发确认原先出现 `project_incremental_preview_stale`；确认创建/提交串行化后两请求返回同一任务、无重复发送：`s07-concurrent-green.log` 7 passed / 3.92s。
- 恢复新轮原先丢失单工作 not_before 并提前发送；恢复和项目增量复制原退避截止值：`s06-backoff-copy-green.log` 14 passed / 6.83s。公开预览只输出可序列化时间值。

#### 本次 RED → GREEN

测试均通过已确认的公开服务或 CLI/Web 边界；命令 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest <文件> -q -o addopts=''`，Python 3.10.10 / macOS。日志在忽略的 `.verification/`。以下组合有重叠，不累加为总测试数。

| 日志前缀 | 可观察缺陷与修复 | GREEN |
|---|---|---|
| s08-cli-budget | 两个 CLI 接受但丢弃证据/变体/并发/重试选项；现全部传给 CollectionOptions，预览保留输入 | 1 passed / 0.64s |
| s08-native-budget | HTML 把中文标签写入 input name，真实表单提交忽略预算；修正稳定字段名，测试解析实际 HTML 后提交并检查确认请求 | 2 passed / 1.29s |
| s07-selection | 不存在或不属于所选入口/身份的 important_asset_ids 静默忽略；现明确拒绝，不披露其他身份资产 | 5 passed / 2.19s |
| s08-important-cli | 缺少重要资产选项；新增可重复 --important-asset-id，已观察资产可明确优先重访 | 1 passed / 0.54s |
| s08-work-selection | 网页不能指定恢复工作；表单增加工作 ID、表格显示 ID，确认后只发送指定项 | 1 passed / 0.61s |
| s08-global-worker | 两服务各自线程池超过全局任务上限；相同数据库匿名/身份调度共用有界池，各服务关闭等待自身任务、最后一个释放池 | 5 passed / 3.22s |
| s06-directory | 文件 O_NOFOLLOW 未保护祖先目录，目录链接可绕过保护；逐层 dir_fd + O_DIRECTORY/O_NOFOLLOW 打开，写入仅 chmod 已打开私有根、文件 fsync 后才返回引用 | 22 passed / 7.14s |

存储现拒绝私有根或任何祖先符号链接，不会 chmod 链接指向的目录；读文件还校验为普通文件。此行为用于保守拒绝路径歧义，不承诺 Windows 等未测平台兼容。共享线程池对同一数据库的冲突 worker 配置明确拒绝，不能默默提升上限。

#### 包安装检查点（不是最终 v0.8 包验收）

本轮之前的代码用 `python -m hatchling build -t wheel -t sdist -d .verification/package-checkpoint` 构建，版本仍 **0.7.0**。wheel 287 项、sdist 474 项，包含 0010、队列服务/CLI/模板，无运行 data/output/.verification/.worktrees。产物早于本次预算/线程池/存储修复，最终须重新构建。

`/opt/homebrew/bin/python3.12 -m venv /private/tmp/garden-v08-package-smoke`，干净安装上述 wheel，安装日志 `package-install-checkpoint-retry.log`。首次 pip 因代理环境缺 SOCKS 支持失败，重试仅对该命令取消代理变量，使用 `/private/tmp/garden-v08-pip-cache`，未改用户配置。安装环境 Python **3.12.14**，依赖重新解析，与工作区测试环境不同。

从 `/private/tmp` 调用安装环境 `garden --version` 得到 Garden 0.7.0；`garden queue --help` 列出新命令；模块实际来自该 venv site-packages。空数据库 0009→0010 成功，doctor --json 前后数据库字节相同、迁移检查为 ok。**doctor 退出 1**：新安装 Playwright 所需 Chromium 不存在，相关运行目录为未创建/未知；不能描述为整体 doctor 健康通过或已验证该环境浏览器。没有为冒烟额外安装浏览器。

#### 当前未核销

第二轮全量日志 `.verification/v08-full-pytest-second.log` 正在执行，尚无最终结果。第一次的失败保留为未解释问题。

继续核查发现新建任务入口尚有缺口：API ScanOptions 可传 durable，但正式 garden scan 与匿名/身份原生预览尚未提供完整显式开启和预算表单；固定三身份预览对 durable 的早期拒绝须补。它们属于原 S08 授权范围，不能将仅恢复页面完成称作入口全覆盖。

其余包括：确认最后写入前绑定复核、实际执行 AuthSession 绑定；浏览器传输失败重试与同 URL 并发响应归属；身份后处理附加证据预算；完整指标与报告一致性；全扫描事务崩溃和万级实测；最终双评审/版本/安装与文档。Linux 仍无可用 Docker daemon，未验证。此前 AC 差额表中已补的并发确认、not_before、全局池和父目录链接以上述新证据为准，其余不能自动核销。

第二轮全量实际完成：`.verification/v08-full-pytest-second.log`，**878 passed / 2 warnings，801.27s**，退出 0。两条为 websockets/uvicorn 弃用提示。该轮冻结代码包含本节预算传递、选择校验、共享线程池及目录保护修复；不包含运行期间新写的下一片入口 RED 测试 `test_collection_entrypoints.py`，不代表新建入口已完成。Ruff check 与已改 Python 文件格式检查、git diff --check 均通过。首次身份旅程偶发失败仍无已确认根因。
