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
