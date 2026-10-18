# v0.8.0 实施分片与 TDD 验收计划

设计：[增量采集与持久调度](../specs/2026-10-11-v08-incremental-scanning.md)。
基线：`8c7de9bcac3f0c7b9ce0dfcd107a6725607c1896`，隔离分支 `codex/incremental-scanning-v08`。
状态（2026-10-18）：S01–S09 本地验收完成，版本 0.8.0；完整回归 910 passed。Linux 未验证；未推送、未创建 PR、未打 tag 或发布。后文按日期保留历史过程，最终核销见文末。

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

### 2026-10-15 新建入口与执行绑定补齐（仍非版本完成）

环境：macOS、Python 3.10.10，隔离分支 `codex/incremental-scanning-v08`，本节修改基于本地 `fac186b`。主工作区改动保留；本次没有推送/PR/发布授权。

- 新建匿名与独立身份的原生表单、预览确认及正式 `garden scan --durable` 入口接入同一 CollectionOptions。预算未显式开启时 CLI 拒绝孤立预算参数；固定三身份预览明确拒绝 durable。复用配置保留旧 None 语义及新预算。
- 表单预算约束由 schema 提供；确认页显示实际生效值。队列窄屏改为带字段标签的卡片，已结束任务不再显示暂停按钮。
- 确认竞态：SQLite 写锁被另一事务占用期间修改 Target，原先过期预览仍创建任务。RED 实际未抛 ConflictError；现在匿名/身份创建获得写锁（run flush）后再次 validate，失败回滚且不发布工作。
- 登录绑定：原先排队后导入新 AuthSession，会自动改用新状态并发送 verify/asset/verify 三次请求。上下文执行绑定现包含所选恢复会话 ID、目标、私有材料引用、身份格式元数据及到期配置，排除正常健康校验会变化的状态/时间；执行前与每次发送门检查绑定。排队期间替换/撤销返回 identity_binding_changed、零发送、不完整；不会匿名降级。已结束历史报告不重写。
- 浏览器传输失败：非法 HTTP 合成站点原先只有一次发送，不执行已授权重试。CDP loadingFailed 现将关联工作按持久 cooldown/重试额度延期，额度耗尽为 failed；显式 canceled 不自动重试。真实 Chromium 两次发送、零响应、工作 failed，预算和未知覆盖保留。

本节 TDD/回归均使用已确认公开边界；命令前缀 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest`，参数 `-q -o addopts=''`。组合有重叠，不累加。

| 日志（忽略目录 .verification） | 实际证据 |
|---|---|
| s08-new-entry-green.log | 新建原生入口与旧预览页：14 passed / 4.49s |
| s08-identity-entry-green.log | 身份原生入口/适配器：8 passed / 2 warnings / 8.61s |
| s08-new-cli-green.log | 正式 CLI 真实子进程/本地服务及旧 CLI：15 passed / 2 warnings / 7.08s |
| s08-mode-preview-green.log | 不支持模式早期拒绝与预览：17 passed / 4.42s |
| s08-entry-regression.log | 入口、无 JS 旅程、身份、队列、CLI、预览组合：61 passed / 2 warnings / 98.99s；早于本节执行绑定修复 |
| s07-write-binding-green.log | 写锁竞态、项目增量、API、恢复：16 passed / 7.38s |
| s08-identity-binding-red.log | 替换状态实际发送 3 次；撤销未返回绑定变化的专用说明；2 failed / 12.22s |
| s08-identity-binding-green.log | 替换/撤销零发送、原身份健康预算、恢复与项目增量真实浏览器：5 passed / 2 warnings / 38.15s |
| s04-browser-transport-red.log | 传输失败未重试：1 failed / 2.64s |
| s04-browser-transport-green.log | 包含暂停/重定向/站点地图的浏览器回归：8 passed / 2 warnings / 22.11s |

测试环境说明：身份入口初轮未获得浏览器运行权限时出现验证失败，获得权限重跑通过；不将环境差异描述为已修复产品缺陷。身份绑定测试初版误用仅支持固定三身份的 get_assessment，改为公开 get_scan 后重新执行并保留有效 RED。两条 warnings 为既有 websockets/uvicorn 弃用提示。

#### 实际原生页面验收

Playwright CLI 使用本机 Chromium 1208，关闭 JavaScript，390×844。从首页开启持久队列、将发送预算设为 2，经预览确认到队列页；观察到 2 次发送、2 个响应、1151 证据字节、2 条 observed。页面服务是真实 FastAPI，目标业务响应由合成 MockTransport 提供，不能当万级真实 HTTP 验收。

`output/playwright/v08-new-queue-mobile-final.png` 已实际查看，卡片 URL 可读；`.verification/v08-entry-mobile-width.txt` 实测 document width 与 viewport 均为 390。移动预览截图与提交/队列文本快照留于本地忽略目录。桌面截图本轮已查看，但采集于最终按钮/窄屏样式修复前，只作为旧界面参考，不计最终桌面验收。两次 Playwright 会话已关闭，UI fixture PID 78470 已停止并经 ps 核实不存在。

第三轮 `.verification/v08-full-pytest-third.log`（`--basetemp=.verification/full-third-temp`）因发现新身份绑定误阻重新登录后的新轮预览而主动中断，进程最终退出 143，不记为通过。中断前另有三身份真实浏览器失败；保留数据库显示 run incomplete / overall_timeout，阶段 establish_contexts，三个上下文 ready 但 completeness pending。独立复测另记，不据此认定环境原因。该轮浏览器与后续 RED 曾有短暂重叠：SIGINT 未及时终止 pytest，随后 SIGTERM 确认退出；不把这段测试作为干净串行全量证据。

新轮兼容修正：稳定范围 binding 保持原语义；身份材料摘要放在上下文 identity_snapshot.collection_identity_binding，由新队列创建时绑定，发送时校验。历史身份改变不阻止恢复/项目增量的新预览；确认令牌仍绑定当前会话，新轮重新验证身份。`s08-identity-reconfirm-red.log` 为 1 failed / 1 passed / 8.97s，GREEN `s08-identity-reconfirm-green.log` 为 18 passed / 2 warnings / 46.26s（身份、恢复、项目增量）。早期 5 passed 不能代替这次兼容回归。

包装中间检查：`.verification/package-entry-binding` wheel 288 项/sdist 477 项，新增表单、服务、CLI 及 0010 迁移包含在包内，运行产物不入包。wheel 的迁移按既有 force-include 位于 app/db/migration_assets 下；初次清单脚本按源码路径检查失败，纠正检查路径后通过，没有修改打包配置。将 wheel 安装至既有 Python 3.12.14 冒烟环境（不是新建的全新环境），从 /private/tmp 验证 site-packages 来源、CLI 新参数、模板与迁移，日志 package-entry-binding-smoke.log。80 列 help 会省略长选项，检查改用 COLUMNS=180 后通过。产物早于最终身份绑定分离修正，仍不是最终验收包。

#### 保留的验收缺口

首次全量身份旅程偶发失败仍无确认根因。登录绑定已补，但并发同 URL 浏览器响应归属、身份后处理证据字节预算、完整统一效率指标仍待实现/验收。完整扫描资产/证据事务崩溃实验、万级真实 HTTP/浏览器、最终双评审、最终包与干净安装、版本与展示文档尚未完成；Linux 无可用 Docker daemon，未验证。旧 v0.8 开发队列若缺少新身份绑定，将保守拒绝继续执行，需重新预览创建新轮；不回填伪造历史绑定。版本仍为 0.7.0。

三身份超时独立复测：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_authenticated_coverage_e2e.py -q -o addopts='' --basetemp=.verification/auth-coverage-repro-temp`，日志 `v08-auth-coverage-repro.log`：1 passed / 2 warnings / 13.61s。没有调整 60 秒预算或放宽断言；一次复测通过不视为根因已解决。身份分离修正后启动第四轮完整回归，日志 `v08-full-pytest-fourth.log`，现场 `full-fourth-temp`。

最终身份分离修正后的本片包重建于 `.verification/package-identity-binding-final`（“final”只指本片冻结点，不代表 v0.8 最终交付）。wheel/sdist 中 collection_queue.py、identity_collection.py、新表单逐字节匹配当前工作区；wheel 包含迁移资源。SHA-256 见该目录 sha256.json。新 wheel 已覆盖安装至既有 Python 3.12 冒烟环境；没有把重复安装称作新的干净安装或浏览器验收。

第四轮全量真实结果：**885 passed / 1 failed / 2 warnings，736.39s，退出 1**。唯一失败 `tests/test_identity_collection.py::test_single_user_and_two_same_role_identities_are_isolated`，断言资产全部 confirmed 不成立。保留现场 full-fourth-temp/test_single_user_and_two_same_0/garden-test.db：两个 run 均 completed/complete，但第二轮 profile:1 的 /asset 为 identity_uncertain；相关证据采集时间比该身份最后一次校验晚约 6ms。此时间差是观察，不是已经确认的因果。该轮不包含运行中新增、随后移到忽略目录的 finalization 原型测试。

公开服务延迟响应原型 `.verification/test_identity_finalization_prototype.py` 首次运行 1 passed / 3.75s，未复现目标失败，不当作 RED 或修复证据。原失败用例六次独立串行复测 `.verification/identity-isolation-loop.log` 全部通过（各 6.27–6.55s）；仍未确认根因，不放宽断言。扩大串行复测另存 identity-isolation-stress.log，首个失败停止。当前不得声明完整回归通过或本条已最终验证。

#### 身份收尾竞态：复现、根因与修复

扩大串行复测在第 15 轮再次捕获相同断言失败（identity-isolation-stress.log，5.90s）。最小用例通过公开 IdentityCollectionService + AssetCatalogService，使用本地合成站点延迟响应头；身份 verifier 为可控合成适配器，不声称该最小用例独立验证真实登录健康。`identity-finalization-headers-red.log` 1 failed / 3.03s，重复 `identity-finalization-repeat-red.log` 1 failed / 3.14s。

临时仅记录事件名称的 trace（identity-finalization-trace.log，1 failed / 3.76s）顺序为 response → proof begin → proof end → close begin → response → close end。确认 Playwright 在 context.close() 泵事件时仍调用响应落库，导致末批身份验证后出现新观察。此前单看数据库的 6ms 时间差并不足以确定因果，本次事件顺序提供了实际证据。

BrowserDiscovery 现在进入收尾后拒绝新增发送，按请求超时与总超时中更短者等待已发送请求完成；超时设置 response_settle_timeout。随后移除响应/请求完成监听器、保存已有响应元数据，最后验证身份，关闭期间不再追加未确认观察。全部 DEBUG-v08finish 探针已移除。不是简单把 identity_uncertain 改成 confirmed，也未放宽原测试断言。

修复初轮 `identity-finalization-green.log`：最小用例与原 identity_collection 全文件 **8 passed / 2 warnings / 140.41s**。原型仍留在忽略目录作为实验记录；正式回归位于 tests/test_identity_finalization.py。新增超时场景与匿名/身份持久浏览器组合结果另记，最终全量仍待新代码冻结后执行。早先包产物不包含本次 BrowserDiscovery 修复，不代表最终代码。

收尾覆盖回归：`tests/test_identity_finalization.py tests/test_collection_browser_e2e.py tests/test_collection_identity_e2e.py`，日志 browser-finalization-regression.log：**15 passed / 2 warnings / 73.18s**。新增收尾超时场景验证：迟到响应不会在最终证明后进入资产记录，context.error_code=response_settle_timeout、completeness=incomplete；正常收齐则 confirmed/complete。初次组合命令误写不存在的 test_browser_discovery.py，未执行测试，纠正后上述文件的结果才计入证据。

第五轮完整回归启动：v08-full-pytest-fifth.log / full-fifth-temp。代码冻结，Ruff check 全仓、当前改动格式检查与 git diff --check 通过。新的包产物 package-finalization 包含收尾修复；创建 /private/tmp/garden-v08-finalization-smoke Python 3.12 新环境进行独立安装，尚待结果。既有环境的浏览器测试与新环境安装不混记。

第五轮完整回归最终结果：**888 passed / 2 warnings / 820.13s，退出 0**。命令 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -q -o addopts='' --basetemp=.verification/full-fifth-temp`。包含本节新建入口、最终写锁绑定、身份执行绑定/新轮兼容、传输重试、浏览器收尾及两个正式收尾回归；两条为既有 websockets/uvicorn 弃用提示。第四轮 confirmed 断言失败已有稳定最小 RED、事件顺序根因和修复证据；更早三身份建立超时及首轮旅程警告仍不冒称同一根因已修复。

本片干净安装完成：全新 `/private/tmp/garden-v08-finalization-smoke`，Python 3.12.14，安装 package-finalization wheel 并重新解析依赖（清单 package-finalization-dependencies.txt，含 Playwright 1.64.0 / FastAPI 0.143.1）。从 /private/tmp 调用已安装模块，确认 site-packages 来源、新 CLI 参数、模板与迁移；0009→0010 成功，doctor 前后数据库 SHA-256 相同、数据库检查 ok。doctor **退出 1**，新 Playwright 对应 Chromium 缺失，部分目录尚未创建；未在此环境跑浏览器。未为了安装修改用户代理/缓存配置。wheel 288 项、sdist 478 项，修复源码逐字节匹配；运行产物未打包，完整摘要见 package-finalization/inventory.json。仍为 0.7.0 开发检查点，不是 v0.8 发布。

后续规模实验计划使用独立合成数据库/本机 loopback：10,000 工作去重分页及新进程读回；真实 HTTP/浏览器各分轮采集，保留每轮旧 max_pages/max_browser_requests 上限。实验未执行前不计 V08-AC11 通过。

### 2026-10-16 规模实验基线（持续验收）

冻结产品代码 `1984ac2`，macOS、Python 3.10.10；独立合成数据库，主工作区未改动。下列实验不是发布或生产 SLA。

10,000 工作队列实验：`.verification/queue_scale_experiment.py` / `queue-scale-experiment.log` / `queue-scale-result.json`。插入 21.860s，重复入队去重 3.288s；新 Python 进程分页读取 50 × 200 条、核对顺序/唯一 ID/私有请求样本和上限抑制，0.855s。10,000 条 pending、0 次发送，coverage_complete=false；数据库 6,963,200 字节，私有文件 10,001 个，父 Python 进程峰值 RSS 96,501,760 字节。新进程只读验证不是服务自动恢复执行证明，也不将 pending 解释为已覆盖。

真实 HTTP 预跑：1 × 100 URL，100 次实际请求、100 条 observed，3.769s（包含收尾），26.53 请求/秒；数据库 1,396,736 字节，Python 峰值 RSS 115,752,960 字节。使用真实 loopback HTTP 服务，显式 min_interval_ms=0，重试为 0；预跑结果单独保留于 http-scale-pilot-result.json。正式 20 × 500 URL 实验另记，保留旧每轮 max_pages=500 等上限，不改成单轮无限采集。

HTTP 正式结果（同一冻结产品 SHA）：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python .verification/transport_scale_experiment.py --transport http --rounds 20 --per-round 500`，http-scale-10000.log / http-scale-result.json。20 轮全部 completed，每轮 500 次实际请求、500 条 observed、coverage_complete=true；共 10,000 个不同 URL 和 10,000 次实际请求，总耗时 826.957s，12.093 请求/秒，Python 进程峰值 RSS 140,787,712 字节，数据库 43,614,208 字节。HTTP loopback 服务与持久队列真实执行，无目标 MockTransport。等待/冷却耗时尚未单独测量，浏览器基线尚待执行，因此仍不整体核销 AC11。

并发响应 RED：新增 tests/test_browser_concurrent_response.py，通过公开 ScanApplicationService + CollectionQueueService，合成站点并发请求同一 /api，先返回 503、后返回 200。首次夹具漏传 ScanPipeline 的 policy/gateway，TypeError 不计产品 RED；纠正后 browser-concurrent-valid-red.log 为 1 failed / 2 warnings / 3.75s，队列为 failed:1/observed:1，预期 observed:2。重复 trace 同样失败：第一次响应属于 attempt 2，但 persist 把 503 计到 still possibly_sent 的 attempt 3；随后真实 attempt 3 的 200 被 finish 幂等分支忽略，工作也仍停留 failed。两个独立问题均有事件证据，不归因于收尾漏收。临时 DEBUG-v08concurrent 探针已移除，原型保留于忽略目录。

修复与初步 GREEN：持久浏览器改用 CDP requestId 将响应、正文与 attempt 精确关联；内部关联号是 FetchResult 的非序列化私有属性，不进入公开 DTO。正文缓冲有界，未知长度/编码/超限响应不读取完整正文；已发送的同工作兄弟请求成功时可收敛先前失败状态。普通非持久浏览器路径保持既有实现。browser-attribution-green.log **16 passed / 2 warnings / 76.25s**，覆盖原并发 RED、持久浏览器重定向/暂停/预算/重试、身份收尾等。随后增加相反返回顺序和四种正文边界，browser-attribution-boundaries-green.log **11 passed / 2 warnings / 27.89s**（含旧 discovery_browser）；与上一组合重叠，不相加。正文测试首轮把额外未发送资源误排除，4 failed / 7 passed，正文判断断言已通过；修正为核对观察数和请求预算，不将未知资源伪造为已覆盖。

浏览器 100 URL 预跑：browser-scale-pilot-result.json，100 实际请求/100 observed、队列覆盖完整，8.823s；任务 completed_with_warnings（达到页面上限），不混同任务状态与队列覆盖。正式可复现脚本已加入 scripts/benchmark_collection_queue.py 和 benchmark_collection_transport.py，说明见 docs/benchmarks/v08-collection.md。新脚本新进程复核已有 10,000 工作成功；浏览器 20 URL 冒烟 20 实际发送/20 observed，2.957s，源代码摘要 6e462a4a75072b23f1e55bec5f51f9e6dcdbfa2b17e3c452aeb458c5f091ee68。脚本采用独立临时 GARDEN_HOME，输出不入库，RSS 只计父 Python。全仓 Ruff check、改动格式检查及 git diff --check 通过；本片尚未运行新的全量 pytest，先前 888 passed 不计本片最终验证。浏览器万级、完整效率指标及其余既列 AC 缺口仍在。

浏览器万级正式实验已完成：冻结 `8842795` git archive 快照 `/private/tmp/garden-v08-browser-baseline-ckqp70wa`，运行 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python scripts/benchmark_collection_transport.py --transport browser --output /Users/an/Documents/Garden/.worktrees/incremental-scanning-v08/.verification/browser-scale-10000-result.json`，退出 0。20 × 500 的所有轮次均 500 实际发送/500 observed/队列覆盖完整；共 10,000 个不同 URL，1951.306s，5.125 请求/秒，数据库 43,372,544 字节，父 Python 峰值 RSS 148,013,056 字节。快照无 .git，输出 base_commit=null，源码摘要与 20 URL 冒烟一致；未运行其他浏览器，同时运行少量非浏览器回归。此基线早于下节指标/预算修改，详见 docs/benchmarks/v08-collection.md。

### 2026-10-16 统一指标与身份材料预算（本地实施）

在 `8842795` 后继续以下公开边界的垂直 TDD，未推送。运行环境仍为 Python 3.10.10/macOS；所有 fixture 为合成。

- 新增 0011，而不回写已使用的 0010：queue.telemetry、attempt.is_retry 均 nullable。旧队列保留未记录，不能伪造重复/耗时/重试/效率为 0。有计量历史拒绝有损降级；doctor 只读检查追踪新 head，历史数据库字节不变断言保留。
- 区分发现阶段的重复候选（既有 DiscoveryLedger 持久快照）与重复入队（精确请求键去重次数），不混用。初版重复入队更新两次 revision 导致 refresh 丢掉一次计数，公开读回测试捕获 1 != 2；改为单次 CAS 后更新计数和来源。
- 观察以已保存的 http_response 证据为单位。匿名可确认身份语境，独立身份区分 confirmed、identity_uncertain、auth_diagnostic 和缺少判断；只查本上下文。比例为本轮已确认观察/发送尝试，0 分母或旧队列为 null，不把观察称为业务有效资产。按 HTTP/browser 标明采集方式，验证请求单列。
- 显式重试发送与普通并发重复请求区分：持久 attempt 记录 is_retry；验证请求不继承业务重试标记。排队、活动、等待和其中冷却采用持久墙钟样本，正常关闭冻结；只读投影不前推时间，失联 worker 只保留最后提交样本并标为不完整。活动时间不是 CPU 时间，等待不持有 SQLite 写事务。
- 共享指标投影用于 API/CLI 和 JSON/CSV/Markdown 元数据；网页和扫描 Markdown 报告使用同一中文指标格式。新增项目主体/观察指标仍待实现，不能把本片称为完整指标验收。

本片关键日志（.verification，组合有重叠，不累加）：

| 日志 | 实际结果 |
|---|---|
| metrics-duplicate-red | 缺少计数，1 failed / 0.48s |
| metrics-duplicate-green | 初版新来源重复漏计，1 failed / 13 passed / 4.31s |
| metrics-duplicate-final-green | 14 passed / 4.07s |
| metrics-migration-green | 升级、拒绝有损降级、doctor、迁移组合 60 passed / 30.03s；当时 0011 尚未加入 is_retry，最终回归另记 |
| metrics-observations-red / green | 缺少公开观察指标；GREEN 4 passed / 1.62s，含实际 HTTP 适配器→API/网页/报告一致性 |
| metrics-identity-red | 缺少 verification_requests；fixture 已验证身份四类计数与上下文隔离，1 failed / 0.45s |
| metrics-projection-green | 25 passed / 8.77s |
| metrics-timing-red / green | 缺少计时操作；GREEN 22 passed / 7.17s |
| metrics-retry-red / green | 缺少重试指标，1 failed / 1.54s；GREEN 23 passed / 8.34s，包含实际等待约 1 秒的退避 |
| metrics-adapter-browser-regression | 90 passed / 2 warnings / 120.99s，含最终 0011、CLI/API/迁移/doctor、真实浏览器并发双顺序/正文边界/身份收尾 |

身份材料预算新 RED：tests/test_collection_identity_evidence_budget.py，通过真实 Chromium 与公开匿名/身份采集入口，以匿名基础响应的计费字节 + 1 为身份采集预算。原先仍保存身份响应/请求材料且 queue.coverage_complete=true，identity-material-budget-red.log **1 failed / 2 warnings / 5.15s**。

修复在基础响应事务内先合入身份评估字段，并预留受保护原始请求 JSON 与可展示 ScanRequest 内容的序列化字节；预算通过后才写响应对应的请求材料。计费不包含数据库页/索引、关系 ID、时间戳或存储路径开销；identity_uncertain 后变为较短 confirmed 时不退款，属于保守计费，并非磁盘占用。预算失败回滚本次资产/证据，仍保留已经发生的发送/响应计数；不标已覆盖。

首轮 identity-material-budget-green 为 **1 failed / 7 passed / 2 warnings / 52.83s**：预算/回滚行为已通过，测试误将必需的 context-0 初始输入材料也当成响应材料。改为检查真实上下文的响应材料，未删除初始化输入或放宽预算断言。随后 identity-material-diagnostic-red 捕获公开 error_code 仍为泛化 identity_collection_failed；现对可信 collection_evidence_budget 提供明确预算说明并标为 incomplete，其他未知异常仍使用脱敏通用说明。最终组合结果待回填。

剩余重点：新增项目主体/观察及报告终态一致性；完整扫描资产/证据事务的崩溃窗口、存储孤儿材料审计；最终完整 pytest、最终双评审、当前包/干净安装、版本/README/展示页和最终 UI 旅程。Linux 仍未验证。此处未将历史 888 passed 记为本片全量结果，版本仍 0.7.0。

身份材料预算最终组合：identity-material-final-green.log **8 passed / 2 warnings / 54.12s**。计费、回滚、私有响应材料未写入、专用 error_code 以及既有身份恢复/收尾通过；两条仍是 websockets/uvicorn 弃用提示。

当前代码冻结后启动第六轮完整 pytest：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -q -o addopts='' --basetemp=.verification/full-sixth-temp`，日志 v08-full-pytest-sixth.log。未将正在执行记为通过。冻结期间构建 package-metrics-budget 的 wheel/sdist，版本仍 0.7.0；新采集响应模块、指标/计时模块、身份预算、模板及 0011 迁移逐字节核对源码，运行产物未入包，清单及 SHA-256 在该目录 inventory.json。安装冒烟另记，不替代浏览器或完整回归。

当前包更新冒烟：将 package-metrics-budget wheel 使用 `--no-deps --force-reinstall` 安装到已有 `/private/tmp/garden-v08-finalization-smoke`（Python 3.12.14；不是全新干净安装）。从 /private/tmp 且 PYTHONPATH 为空导入，确认 collection_metrics 来自 site-packages；独立临时数据库 0010→0011 成功，公开队列初始计时为 0、观察比例为 null。doctor 前后数据库 SHA-256 不变，数据库检查 ok；doctor **退出 1**，当前依赖版本对应 Chromium 未安装，报告/runtime 目录未创建，未在此安装环境运行浏览器。日志 package-metrics-budget-install.log / package-metrics-budget-smoke.log；安装未修改用户 pip/cache 配置，缓存不可写提示不影响安装结果。

### 2026-10-16 当前指标片全量回归与真实崩溃窗口

第六轮完整 pytest 已退出 0：**903 passed / 2 warnings / 903.84s**。两条为既有 websockets/uvicorn 弃用提示。此结果覆盖当前 0011、指标/计时与身份材料预算代码，不包含此后新增的实验脚本调整。

真实全流程实验 `.verification/full_pipeline_crash_experiment.py` 使用公开 ScanApplicationService、真实 loopback HTTP 与独立子进程，分别在等待响应、SQL INSERT 证据尚未提交、观察事务已经提交时 SIGKILL（退出 -9）。前三次立即重启均 recovered=0 且没有目标请求；等待真实租约到期 119.449 秒后，三次重启各 recovered=1，仍没有目标请求。前两种窗口没有残留 ScanAsset/ScanEvidence，工作变为 uncertain；第三种保留 1 个资产/1 条证据和 observed。前两种经公开预览/确认恢复成新任务后各新增一次实际发送并 observed，原任务资产/证据保持 0。实际总发送分别为 2、2、1；日志 full-pipeline-crash.log，结果 full-pipeline-crash-result.json。此实验没有用推进测试时钟替代真实租约等待。整理为正式脚本 scripts/experiment_collection_crash.py 后另跑复核，结果另记。

正式万级队列脚本首次完整运行暴露 macOS tempfile 路径含 /var 符号链接而被受保护存储拒绝：queue-scale-current.log，退出 1 / collection_storage_unavailable。仅将新建实验临时根目录 resolve 为真实路径，未放宽产品路径保护。重跑 queue-scale-current-green.log 退出 0：10,000 入队 24.811s，10,000 重复入队 7.951s，新进程 50 页读回 0.919s；0 次发送，数据库 6,963,200 字节，私有文件 10,001，父 Python RSS 92,815,360 字节。当前含 0011 的结果与早期 1984ac2 基线分开记录，不声称直接性能对比。

正式崩溃脚本复核 full-pipeline-crash-script.log 退出 0，同样三窗口、实际等待 119.452s、发送分别 2/2/1；结果文件为正式脚本的最新现场。之后仅将 SQL 字符串拆行满足 Ruff，无语义变更。上述崩溃实验早于以下项目指标收尾实现，不能作为新增收尾事务的进程中断证明。

### 2026-10-16 项目入库增量与收尾快照

公开 ProjectIngestionService RED：project-ingestion-metrics-red.log，1 failed / 0.53s，缺少 ingestion_metrics。实现将新增主体/观察与成功入库快照放在同一事务，重复导入不重复累计；同一主体再次观察新增主体 0、新增观察 1。按首次创建上下文归因新增主体，多身份不重复计算主体。project-ingestion-metrics-green.log 26 passed / 8.74s。

扫描收尾先完成既有自动项目入库，再冻结每个队列的 project_ingestion 快照并生成最终报告；未关联项目为 not_applicable，失败/旧无指标入库为 unknown/null，不伪造 0。收尾快照有一次性标记，后续项目重试/再次 reconcile 不改写旧报告和指标；当前项目索引状态仍由项目自身展示。收尾失败保留未记录，并使用通用日志，不改变源扫描结果。此设计将扫描当轮结果与之后独立重试明确分开，未改变 v0.7 入库失败隔离语义。

project-finalization-red 首次夹具误把 durable 放在请求顶层，未实际开启持久队列，不计有效 RED；修正为 request.options 后 project-finalization-valid-red.log 1 failed / 0.58s（最终报告缺少新增指标）。project-finalization-green.log 27 passed / 9.79s。新增真实 SQLite 入库失败触发器验证失败时计数 null、扫描仍 completed，显式重试成功后历史报告字节/队列指标不变。第一次组合命令误写测试文件名，没有执行测试；纠正后的 project-finalization-failure-final.log **34 passed / 12.73s**，包含项目增量计划回归。全仓 Ruff check、改动格式和 diff 检查通过。

新增收尾代码冻结后启动第七轮完整 pytest，日志 v08-full-pytest-seventh.log；尚未将其记为通过。当前 HEAD 仍 8842795，本片改动未提交、未推送。剩余包括最终独立审查、当前干净安装、最终 UI/文档展示、存储孤儿审计及完整计时/资源基线；v0.8 尚未完成。

存储孤儿审计 collection-orphan-audit.log：独立临时 SQLite 在 INSERT collection_work 时由真实触发器拒绝（模拟事务失败）；公开 list_work 返回 0、请求数 0，没有发布损坏引用。文件先落盘使目录保留 1 个孤儿，目录 0700/文件 0600；与 queue/work 的已提交 private_ref 集合比对形成私有 orphan-review-manifest.json。移除触发器后重试只生成 1 个 pending 工作，不自动加载孤儿、不发送目标请求。未自动删除文件；维护时必须停止写入并核对所有已提交引用后才可清理，运行中仅凭目录差集删除会误删尚未提交材料。文件与 SQLite 不承诺跨系统原子性，符合规格第 73 行边界。

包含项目收尾的 package-project-metrics：wheel 293 成员、sdist 490 成员，新增模块/模板/0011 与当前源码逐字节一致，运行产物未入包；SHA-256 见该目录 inventory.json。在全新 Python 3.12.14 venv 尝试安装时，宿主 SOCKS 代理环境导致 pip 缺少 SOCKS 支持；首次退出 1，不计干净安装成功。第二次仅移除安装子进程的 SOCKS 环境变量，未改变全局配置，正在下载依赖。独立规范/规格两轴只读评审已启动，不运行浏览器，不将待完成检查记为通过。

第七轮完整 pytest 已结束：**906 passed / 2 warnings / 840.29s**，覆盖当前项目收尾快照。全仓 Ruff check、364 个文件格式检查、git diff --check 通过。此检查点不意味着 v0.8 全部验收完成。

独立评审（只读，基线 8c7de9b 至当前工作区）：
- Standards：1 项 P2，适配器重复直接写工作状态，违反计划第11行深模块约定；浏览器抑制后还读取旧 work 投影。需将状态转换收回队列接口，保留最终投影。
- Spec：2 项，P1 快速重启时旧 lease 尚有效而被跳过，到期后没有再次收敛，预览可成功但确认因 source running 被拒；需在显式恢复写操作安全收敛失效来源。P2 恢复预览缺少原预算、不可恢复计数/原因；一条材料缺失会阻断全部预览。需允许解释可恢复集合并严格绑定确认。未发现确定越界；项目已有周龄优先级，不计缺失。

上述评审问题尚待 TDD 修复，不将评审完成等同于通过。新一片开始前保留本轮已通过的代码检查点。

### 2026-10-17 评审修复进行中

已保存本地检查点 `02c5a37`（Record durable collection metrics and freeze project outcomes），对应上一轮 906 passed；未推送。全新 `/private/tmp/garden-v08-clean-install-ex_26uev/venv` Python 3.12.14 安装已成功，使用安装时实际解析的依赖（含 SQLAlchemy 2.1.4 / FastAPI 0.143.3 / Playwright 1.64.0），不是复用旧 venv。独立 cwd、空 PYTHONPATH 下真实 loopback HTTP 扫描 1 次发送/1 条确认观察，项目未关联显示不适用；API、HTML 与报告一致，模块来自 site-packages。doctor 前后数据库 SHA 不变；doctor exit 1，未安装对应 Chromium，故未声称安装环境浏览器通过。日志 clean-install-project-smoke.log / clean-install-project-api-smoke.log。此包是 02c5a37 实施内容，不含后续评审修复；版本仍 0.7.0。

快速重启恢复 RED 首次夹具 URL 拼接缺斜线，不计产品失败；修正后在原检查点确认 `collection_source_active`（restart-resume-valid-red.log）。恢复确认现先持有执行器，并在数据库写锁内核实所有同源任务上下文的租约已失效，再收敛原任务与租约、创建关联子任务；读预览不改变任务。restart-resume-final-green.log **19 passed / 6.44s**。新脚本参数 --resume-without-restart 正进行真实 SIGKILL/租约等待验证，不把可控时钟测试替代实机结论。

恢复材料预览 RED：单条私有材料删除导致整个 preview 抛 collection_payload_unavailable。现在返回可恢复集合、不可恢复条目/原因、原轮预算与选中工作未完成原因；确认绑定实际可恢复集合，材料恢复或丢失会使旧 token 失效。全不可恢复时 start 拒绝创建空子任务，CLI 不再发出空任务确认提示，网页没有确认按钮。页面预算显示 RED 与 CLI 空集合提示 RED 均已观察，recovery-preview-final-green.log **16 passed / 7.60s**（API/CLI/HTML/恢复/启动组合）。

规范评审深度投影问题通过公开 CollectionBrowser.enqueue 复现：深度越界工作已 suppressed，但返回 True；browser-depth-admission-red.log。状态规则统一到队列服务仍待完成，尚未对这些评审修改运行新的完整回归。


### 2026-10-18 最终收口与 AC 核销

产品基线 `02c5a37` 加本片评审修复。规范和规格两位只读评审者复核三项修改后均无剩余具体问题。队列服务集中 claim、完成、身份评估和深度抑制规则；浏览器 enqueue 返回最终状态，不再返回过期的“可接受”投影。针对性 review-fixes-browser-regression.log：22 passed / 2 warnings / 89.83s；队列/HTTP 组合 14 passed / 4.98s。此前恢复组合 19 passed、预览组合 16 passed 仍适用。

快速重启真实实验 `scripts/experiment_collection_crash.py --resume-without-restart` 退出 0：实际等待 119.399 秒，前两个 SIGKILL 窗口无需二次服务启动即可显式创建补采子任务；旧资产/证据不变，总发送 2/2/1。没有自动访问目标。日志 fast-restart-crash-experiment.log。

人工界面验收使用 Playwright CLI，禁用页面 JavaScript，浏览器工作串行：390×844 恢复预览、确认新轮成功，页面宽度 390；1440×1000 指标展开截图已查看。运行中暂停的最初两次夹具等待超过租约，不计正常收尾通过；第三次在浏览器中受控启动后立即暂停，实际 1 次发送/1 条 observed/1 条 pending，队列 paused、coverage_complete=false，559 字节证据，新发现链接未发送。快照和截图留在 output/playwright，API 证据 pause-ui-result.json。产品租约与范围未放宽。

真实 HTTP/Chromium 各 20 URL、500ms 间隔样本完成：最小服务端间隔 0.510/0.501 秒，等待 9.010/8.395 秒，发送与观察均 20。实验产品源码摘要 `0c5395b8fa9b7a528bfe077807489815a036d02bebc0cc75665f1030df7a12e1`，完整数值及历史万级基线见 docs/benchmarks/v08-collection.md；小样本不冒充新的万级结果。

冻结产品后的第八轮完整命令：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -q -o addopts='' --basetemp=.verification/full-eighth-temp`。v08-full-pytest-eighth.log 退出 0：**910 passed / 2 warnings / 740.94s**；仅既有 websockets/uvicorn 弃用提示。随后仅将 pyproject/settings/CLI 版本断言从 0.7.0 统一为 0.8.0，tests/test_cli.py **7 passed**；未将版本变更后称作重新全量执行。全仓 Ruff check、365 文件格式检查、git diff --check 通过。

打包与安装：使用 `python -m hatchling build -d .verification/package-v080`（一次误用 `-t wheel,sdist` 被拒，未产生有效包，随后按默认双目标成功）。wheel 293 成员、sdist 492 成员，275 个 app 文件与迁移资源逐字节一致；运行数据、.verification、output 和 worktree 未入包。最终哈希保存该目录 inventory.json，文档更新后 sdist 重新构建核对。

干净安装过程：最初从全新 Python 3.12.14 venv 安装依赖成功；当前 wheel 在该隔离环境中重装验证，不冒充再次全新安装。初次安装宿主 SOCKS 代理失败后，仅移除子进程 SOCKS 变量重试，未改全局 pip/cache。最终 0.8.0 从 /private/tmp、空 PYTHONPATH 导入 site-packages，0010→0011 成功，真实 Chromium 156（Playwright 1.64.0）扫描 1 次请求/1 个确认观察，API/HTML/报告一致；`garden version` 输出 Garden 0.8.0。日志 package-v080-smoke.log。doctor exit 2：runtime 目录未创建故写能力 unknown；数据库/安装/Chromium 检查 ok，数据库哈希未变。此未知不等于采集失败；未为诊断强行建目录。

| 条件 | 最终证据与结论 |
|---|---|
| AC01 | 迁移/降级/旧值测试、包内迁移逐字节检查、安装环境 0010→0011；通过。 |
| AC02 | 三个真实 SIGKILL 窗口、快速重启后直接恢复、过期 worker fencing 与活 worker 启动保护；通过。 |
| AC03 | 队列去重、独立会话 claim、同时确认幂等、事务失败与孤儿审计；通过。孤儿不自动删除，文件/DB 无跨系统原子性。 |
| AC04 | origin/身份准入、重定向与健康验证计费、有界共享线程、HTTP/浏览器暂停测试及手机真实按钮；通过。 |
| AC05 | 目标/入口/profile/session/配置变化失效、确认锁内复查、重新认证、历史报告和项目快照保护测试；通过。 |
| AC06 | known_only/continue_discovery 范围、选择抑制项、缺失材料说明、退避复制与关联新轮；通过。 |
| AC07 | 参数配额、深度/预算抑制、稳定优先级和项目老化、10,000 工作唯一分页；通过。 |
| AC08 | Retry-After 秒/日期/坏值/远期、有界退避/重试、传输失败与重启/取消回归；通过。 |
| AC09 | 正文边界、身份材料计费、同 URL 并发响应归属、敏感值脱敏和路径保护；通过。 |
| AC10 | API/CLI/Web/导出统一指标、项目收尾快照、未知与不适用、无 JS 手机预览/暂停/恢复/查询；通过。 |
| AC11 | 历史冻结代码真实万级 HTTP/Chromium + 当前万级队列 + 当前计时小样本；通过。父 Python RSS 不代表 Chromium 全进程树，未承诺 SLA。 |
| AC12 | 最新 910 项全量、Ruff/格式/diff、两轴复核、wheel/sdist、干净环境和成品安装冒烟；通过本地验收。Linux 未执行，保留限制。 |

README、展示页与 v0.8 操作文档按实测内容同步。剩余交付动作仅本地提交；本次没有远端推送、PR、tag 或软件包发布授权，不自动执行。后续远端 CI/合并和 Linux 验证需分别提供真实证据。本次没有修改主工作区原有未提交内容。

耗时复盘：本版把持久调度、恢复、身份边界、项目指标和全端展示集中交付；评审发现真实缺陷后反复修改冻结点，累计八轮全量，每轮约 12–15 分钟。万级浏览器实验约 32.5 分钟，真实租约实验每轮需等待约两分钟；另有工具会话卡住、代理依赖下载和人工夹具过期造成额外时间。执行上应更早固定分片完成门、集中最终验证，并把后续版本拆成更小的可交付范围；不以无差别增加检查次数替代明确验收。
