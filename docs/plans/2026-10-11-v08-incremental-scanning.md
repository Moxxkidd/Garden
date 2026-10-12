# v0.8.0 实施分片与 TDD 验收计划

设计：[增量采集与持久调度](../specs/2026-10-11-v08-incremental-scanning.md)。
基线：`8c7de9bcac3f0c7b9ce0dfcd107a6725607c1896`，隔离分支 `codex/incremental-scanning-v08`。
状态：实施前设计完成，TDD 公开接口确认待回复。未写测试/实现，版本仍为 0.7.0。

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
