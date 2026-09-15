# Garden 提交前范围预览与配置检查：设计及 TDD 实施记录

状态：用户已批准按推荐范围实施。产品、回归测试及文档在隔离工作树中实现；最终验证结果见 PR。
基线：2026-09-13 核实 origin/main = 8b6f2a6，PR #25 已合并。

## 1. 本轮目标与范围

让用户在提交前看见实际将使用的入口、身份、预算和已知配置问题。
推荐首轮：已有 CLI coverage 向导 + Web quick 表单；普通 scan 与 non-interactive coverage 不增加确认步骤。
Web 三身份提交和凭据管理需要新增交互流程，建议单独迭代。
不提供自动重试、自动扩大预算、自动切换身份或网络扫描式“预检查”。

## 2. 已核实的现状

- CoverageSetupWizard.run 已有同源 Target 筛选、精确 user/admin 档案筛选及一次提交确认。
- coverage CLI 在 wizard.run 返回后才覆盖 options/source_run_id，因此现有确认看不到最终预算。
- _prepare_existing_profile 遇到过期临时凭据时调用 ensure_valid_for_profile；该调用可以验证、刷新乃至重新登录。
- Web 首页只有 URL 输入，POST /scans 立即启动普通扫描，没有三身份表单。
- start_assessment 在创建任务前校验档案存在、共同 Target 和指定 Target；角色与入口同源等还在上下文建立阶段检查。
- 参数默认值由 ScanApplicationService._resolve_options 结合 settings 解析，不能在页面复制一套默认值。
- quick 的 max_resources 限制静态资源采集；认证采集将它传为 InventoryBuildControls.max_requests。

代码入口：
- app/cli/coverage.py、app/cli/coverage_wizard.py
- app/services/scan_application.py、app/services/context_collection.py、app/services/sessions.py
- app/api/routes/scans.py、app/templates/index.html
- tests/test_coverage_wizard.py、tests/test_coverage_cli.py、tests/test_assessment_application.py

## 3. 用户可见契约

### 3.1 三种独立状态

1. 配置检查：匹配 / 需修正 / 无法确认。
2. 登录状态：本次尚未验证；只有实际验证发生后才能称为已验证。
3. 覆盖状态：开始前尚未产生；不得用“预检查通过”承诺覆盖完整。

“配置匹配”只表示结构关系和本地可解析配置符合要求，不证明密码正确、会话有效或目标可达。
未解析的动态配置应标为无法确认，不能默认通过。

### 3.2 展示字段

- 规范化入口、同源范围；摘要中的查询值、userinfo、fragment 按现有脱敏规则处理。
- 模式：quick 仅匿名，coverage 固定匿名/普通用户/管理员。
- Target 和档案 ID、角色及名称；不展示用户名、secret_ref、密码、Cookie 或会话载荷。
- 解析后的页面上限、深度、超时及重试设置，明确上限而非承诺采集量。
- quick 的静态资源预算和 coverage 的请求记录预算采用不同说明。
- 身份采集预算按上下文生效，不将“三身份 × 页面数”显示为总网络请求保证值；登录、重定向、验证与重试另有请求。
- 动态默认值若与最终执行环境不同，应显示“提交时解析”或要求重新预览，禁止冒充精确执行值。

### 3.3 阻止继续与允许继续

阻止新预览流程继续：非法 URL/参数、档案缺失、两档案 Target 不一致、角色错配、入口与 Target 不同源、已支持登录配置不可解析或地址越界。
允许继续并提示：尚未验证登录、尚未执行 DNS/连通性检查、未来仍可能达到采集限制。
缺少管理员凭据不能自动降级为匿名或用户扫描。
旧 API 和非交互命令维持现有契约；将全部旧入口改成更早拒绝，需要另行评估失败任务记录和退出码的兼容性。

## 4. 交互方案

### CLI coverage

选择/创建 Target 和两身份档案 → 汇总最终 options/source_run_id → 配置检查 → 增强现有确认 → 会话准备 → 提交。
保留一次最终确认，增加“返回调整”，取消保持当前取消语义。
确认前仅做本地准备和配置检查；把现有可能联网的会话验证/刷新移至确认之后。
必须保留有效受保护会话复用：不能为了去掉预确认联网而强制所有人重输密码。
确认后若需要补充密码，仍通过隐藏输入；中断必须清理未提交临时秘密并回滚本轮草稿。
打印的最终设置与提交设置来自同一个已解析请求，不在确认后静默改参数。

### Web quick

URL + 可展开的预算选项 → “预览扫描” → 摘要及内联错误 → “返回修改”/“开始匿名扫描”。
当前 quick 提交路由/API 的直接调用保持可用，不因 UI 增加预览而强制现有客户端多发一次请求。
建议新增 POST /scans/preview 和 POST /scans/confirm，仅用于新 HTML 流程。
不在 GET 查询串携带凭据或敏感入口参数；表单值保留并 HTML 转义。
无 JavaScript 仍可预览、返回、提交；避免依赖禁用按钮完成服务端验证。
最终确认端再次校验，不信任 hidden 字段中的“通过”标记。
若预览后参数或配置发生变化，显示新预览或内联错误，不以旧摘要直接启动变更后的任务。

## 5. 模块设计建议（拟新增接口，非现有 API）

新增 app/schemas/scan_preview.py：
- PreviewIssue(code, field, severity, message)，固定 code 和文案。
- ScanPreview(mode, entry_display, contexts, effective_options, issues, can_submit)。
- 不暴露 secret_ref、配置原文、请求头、原始异常对象。

新增 app/services/scan_submission_preview.py：
- build_preview(session, request, settings) -> ScanPreview。
- 使用调用方 session，允许 CLI 检查尚未提交的草稿记录。
- 只读本地配置和关系；不创建 ScanRun、领取临时秘密、写 AuthSession、调用 dispatcher、DNS、HTTP 或浏览器。
- 复用现有规范化和解析能力；必要时抽取最小的公共参数解析/同源比较函数，不复制规则。
- 不调用 TargetNetworkPolicy.preflight 或 AuthSessionService.ensure_valid_for_profile。

确认凭证不是本轮新认证机制。Web 如需要保存待确认状态，仅保存短期不含秘密的预览版本或签名请求标识；真实请求值仍需服务端校验。
避免引入长期草稿数据库、通用策略引擎或新的任务生命周期。

## 6. TDD 边界与逐片实施顺序

已确认测试边界：预览服务公共入口、CLI 完整命令/向导协议、HTTP 表单流程、真实浏览器交互。
使用临时数据库、现有 ScriptedPrompts、HoldingDispatcher、httpx.MockTransport。
不把被测配置检查服务 mock 掉；网络/派发替身用于断言副作用边界。
每次只做一个行为：先写红灯测试并确认原因，再最小实现至绿灯；不要先写完整系统后补测试。

### 任务 1：真实预算预览

文件：拟新增 scan_preview.py / scan_submission_preview.py；必要时最小抽取 scan_application.py 参数解析。
先测显式 max_pages=7/max_depth=1 出现在预览，解析后的默认超时与真实提交一致；再测 retries=0 不被默认值覆盖。
继续测 quick/coverage 的预算标签不同，未验证登录和覆盖状态不出现已验证/完整。
在可控数据库里调用预览两次：任务列表不增加、dispatcher 无提交、网络计数为零、原有会话和临时秘密不变。
绿灯后再补未知配置、非法端口、默认端口等边界。

### 任务 2：身份配置错误可修正

参数化：user/admin 调换、auditor 充当 user、档案删除、跨 Target、入口变更、登录/验证地址越界。
错误必须指向字段并给固定建议，不能回显配置中的秘密。
正常同源变体如 HTTPS 默认端口与大小写不应误拦截；协议/非默认端口不同不得混为同源。
含恶意 HTML 的档案名称在 Web 上只能作为文字显示。
将 user 与 admin 都指向同一档案不能“检查通过”。

### 任务 3：CLI 增强现有确认

先从完整 coverage 命令测试：--max-pages 7 --max-depth 1 在确认前展示，确认后提交保持这些值。
再测取消/返回调整：无扫描创建、无派发；调整后摘要同步变化。
记录会话验证/刷新替身的调用时序：不得早于最终确认。
确认后复用有效会话，不额外索取密码；无效会话按原流程补充秘密。
确认后补密阶段 Ctrl+C、启动 Web runtime 失败：清理本轮未提交临时秘密，保留既有凭据。
非交互 CLI 不读 stdin，不增加提示，已有退出码/--detach/source_run_id 行为保持。

### 任务 4：Web 预览与确认

先测 POST /scans/preview 返回摘要且不创建任务；再实现最小服务端模板。
再测返回修改保留 URL/预算、非法值同页定位错误。
确认合法请求创建一次任务并跳转详情；重复点击沿用现有 active_key 去重语义。
篡改参数、过期预览、预览后配置变化：重新展示或拒绝，不直接启动未确认配置。
禁用 JavaScript 仍能完成；API /api/scans 继续维持原有契约。

### 任务 5：端到端与兼容回归

真实本地测试站：预览期间入口请求数保持零，确认后才进入扫描管道。
浏览器键盘操作、390px 窄屏、返回修改、错误焦点、双击提交。
覆盖现有 P0/P1：计数、100% 提示、unknown、诊断建议、报告下载、取消和失败语义。
不要求全屏像素快照；断言可见摘要、字段值、焦点、实际网络和任务行为。

## 7. 首批测试名字建议

- test_preview_uses_explicit_options_and_runtime_defaults
- test_preview_does_not_create_run_or_contact_target
- test_preview_distinguishes_config_match_from_login_validation
- test_preview_rejects_role_target_and_origin_mismatch
- test_preview_does_not_expose_secret_material
- test_coverage_confirmation_shows_the_request_that_is_submitted
- test_wizard_defers_session_validation_until_confirmation
- test_cancelled_preview_preserves_existing_state_and_cleans_draft_secrets
- test_web_preview_preserves_edits_without_creating_a_run
- test_web_confirmation_revalidates_changed_input
- test_noninteractive_coverage_keeps_existing_submission_contract

## 8. 验证与交付

实施前从已核实的最新 main 创建隔离工作树，保留原工作区未提交文档。
建议交付顺序：PR A 预览服务及 CLI；PR B Web 匿名预览；PR C 演示与文档更新。
PR B 依赖 PR A，不自行合并；也可在用户选择后合并为一个小版本。

本地命令（在实施工作树中设置 PYTHONPATH 为该工作树）：

```sh
/Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_coverage_wizard.py tests/test_coverage_cli.py tests/test_assessment_application.py tests/test_assessments_api.py tests/test_api.py -o addopts='' -q
/Users/an/Documents/Garden/.venv/bin/python -m pytest -o addopts='' -q
/Users/an/Documents/Garden/.venv/bin/ruff check .
/Users/an/Documents/Garden/.venv/bin/ruff format --check .
git diff --check
```

新增测试文件随任务加入命令；真实 Chromium 用具备启动权限的环境执行。
验收以实际结果为准，不预先承诺通过测试数量。


## 9. 实施选择与审阅修正

- 本次将共享预览、CLI 与 Web 作为同一功能分支交付，避免重复迁移共享接口；不包含 Web 三身份管理。
- Web 使用 10 分钟的进程内 HMAC 确认凭证，不保存长期草稿；进程切换或重启要求重新预览。
- 新表单禁用继承的 HTMX boosting，确保 422/409 错误页可见且无 JavaScript 时可使用。
- 确认指纹采用解析后的实际参数，避免未填写默认值与显式默认值之间误报；用户名和认证类型只进入内部指纹，不出现在摘要。
- CLI 确认前刷新配置记录，确认后保留有效会话复用；本轮补密导致的 secret_ref 变化不使配置确认失效。
- Web runtime 启动可能写 SQLite，因此不跨启动阶段持有向导写事务。失败时按原始记录快照与外键引用条件清理；其他任务已引用或已修改的草稿保留，秘密文件仅在数据库清理成功且无人引用时删除。
