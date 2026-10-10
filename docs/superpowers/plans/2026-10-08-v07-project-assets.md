# Garden v0.7 Project Asset Library Implementation Plan

> 执行方式：用户已授权实施，并已移除 Superpowers。使用本地 TDD 流程继续，不再适用旧插件门控。复选框仅在整片验收证据齐全后核销。

**Goal:** 交付跨入口、跨任务的项目资产库，保留历史观察和证据，支持可信变更、关系、可撤销人工整理以及完整用户闭环。
**Architecture:** 在原采集和单任务清单外新增持久化项目索引；来源投影负责脱敏，入库负责幂等和快照，查询／比较／纠正独立服务。原任务和报告不变，终态入库使用独立事务，失败可显式重试。
**Tech Stack:** 当前 Python >=3.10、SQLAlchemy/Alembic、FastAPI、Typer、Jinja、Playwright；不增加依赖或外部服务。
**Spec:** `docs/superpowers/specs/2026-10-08-v07-project-assets-design.md`，用户于 2026-10-08 批准。
**Baseline:** `1529fe6`；设计提交 `1992436`；隔离分支 `codex/project-assets-v07`。

## Global Constraints

- 保留 quick、固定三身份、identity_collection、inventory 及旧 `/assets` 语义；项目成员资格不扩大网络许可。
- 同项目跨任务稳定 ID；跨 origin 隔离；原始观察与证据不可被人工归并销毁。
- 历史任务入库仅接受终态；不完整／失败任务允许保存已有材料，缺失材料为 unknown。
- 预览／查询不访问目标、不读取 protected payload、不隐式写库；确认重验绑定及版本。
- 项目归档禁止新增入口／入库；无硬删除或级联删除源任务。
- 标签每项最长 40 字、每资产最多 20 项；备注最长 2000 字；分页默认 50、最大 200。
- 入库状态 pending/importing/complete/failed；纠正使用可撤销展示规则和追加审计。
- 变更状态 newly_observed/attributes_changed/unchanged/not_observed/unknown；不存在自动 deleted/已下线结论。
- 迁移 0009 不自动导入历史；有项目数据时拒绝有损降级。
- JSON/CSV 分别导出资产／观察／候选／关系，带 schema_version、筛选、exported_count；Markdown 提供概要。
- 用合成 1 万条观察验证完整性，耗时只作环境基线；v0.8 调度和 v0.9 外部集成不在本轮。
- 每片先 RED 后最小实现再 GREEN；完整版本包括六项路线能力及 Web/CLI/API/导出/文档，不因单片完成而结束。

## Review Focus

1. 任务在建立关联前已完成、进程中断后 importing 遗留、同任务并发确认：Task 3/4 验证幂等和可恢复，不让来源任务失败。
2. 乱序入库、缺时间或同一时间响应冲突：Task 3/5 保留真实来源时间和冲突集合，不把入库顺序当作最新状态。
3. 来源任务被删除／终态记录被修改、Target 改 origin：Task 2/3 保留快照并显式拒绝过期确认，不能静默重绑定。
4. 人工纠正与新观察并发，撤销依赖链，标签归属：Task 8 验证版本冲突、无环、原始 ID/观察不丢失。
5. 分页／导出期间入库或纠正，CSV 公式和旧字段缺失：Task 5/9 验证一致快照、无漏行重复、脱敏和 unknown。

## File and Contract Map

新增模型分组：`app/models/project.py`（Project/ProjectEntry/ProjectRun），`project_asset.py`（ProjectAsset/ProjectObservation/ProjectCandidate），`project_curation.py`（ProjectRelation/ProjectAnnotation/ProjectCorrection/ProjectCorrectionMember/ProjectAudit）。
新增 schema 统一在 `app/schemas/projects.py`，服务按职责拆为 `projects.py`、`project_source.py`、`project_ingestion.py`、`project_lifecycle.py`、`project_catalog.py`、`project_comparison.py`、`project_relations.py`、`project_curation.py`、`project_export.py`；不把新功能塞入旧 AssetCatalogService。
适配器：`app/api/routes/projects.py`（API）、`project_pages.py`（原生表单）、`app/cli/projects.py`，模板置于 `app/templates/projects/`。

共用类型：`ProjectSourceRef(source: Literal['scan','inventory'], run_id: int)`；`ProjectEntryCreate(target_id, entry_url, name)`；`ProjectImportRequest(entry_id, source_ref)`；`ProjectSourceSnapshot(scope: AssetScope, records: list[AssetRecord], candidates: list[AssetRecord], comparison_metadata: dict, digest: str)`。比较元数据只含安全配置及覆盖状态，摘要不公开。
查询 `ProjectQuery(view: assets|observations|candidates|relations, site, kind, method, identity_key, project_run_id, tag, q, seen_from, seen_to, sort, order, page=1, page_size=50)`；返回 `ProjectPage(items, matched, counts, limitations, revision)`。资产详情 `ProjectAssetDetail` 包含原始/展示 ID、历史分页链接、身份观察、别名、标签、关系。
预览 `ProjectImportPreview` 与 `ProjectCorrectionPreview` 返回 opaque token、有效期、计数和限制；确认请求不接受客户端替换预览结果。预览令牌 900 秒，单服务最多 100 个（沿用现有预览模式），进程重启失效；数据库版本校验解决竞争，不依赖令牌缓存作锁。

### Task 1: 数据契约、稳定键与无损迁移

**Files:** 新增上述三个模型文件、`app/schemas/projects.py`、`app/services/project_identity.py`、`migrations/versions/0009_project_assets.py`、`tests/test_project_contracts.py`；修改 `app/models/__init__.py`、`tests/test_migrations.py`。
**Interfaces:** `project_asset_key(row: AssetRecord) -> str` 返回版本化形状键／来源隔离键；`project_identity_key(scope: AssetScope, row: AssetRecord) -> str` 返回 target/profile 或来源上下文隔离键。持久主体主键与自然键分开；项目有单调 revision 用于一致确认。

- [ ] RED：`test_project_route_identity` 断言不同 run 同路由键相同；不同 origin/方法/路径大小写/尾斜杠/编码斜杠/重复参数名/Hash 路由不同；缺 origin/方法隔离；公开 ID 不含指纹。
- [ ] RED：`test_project_model_constraints_and_0009_upgrade` 从 0008 种子升级，旧 ID/FK 不变；唯一 project/source/run、项目来源记录键、生效纠正成员约束成立；空项目库可降级，有任一项目数据拒绝降级；项目快照不对源任务使用删除级联。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_contracts.py tests/test_migrations.py -q`，记录新场景因契约未实现而失败，再实现模型、索引、schema 和纯键函数。
- [ ] GREEN 同命令；检查迁移自动发现与序列化兼容；提交本片文件。

### Task 2: 项目、入口与只读来源预览

**Files:** 新增 `app/services/projects.py`、`project_source.py`、`tests/test_projects.py`、`tests/test_project_source.py`。
**Interfaces:** `ProjectService.create(session, name, description) -> Project`、`archive(session, project_id, expected_revision) -> Project`、`add_entry(session, project_id, request: ProjectEntryCreate) -> ProjectEntry`、`update_entry(session, entry_id, request, expected_revision) -> ProjectEntry`；`ProjectSourceService.snapshot(session, ref: ProjectSourceRef) -> ProjectSourceSnapshot`、`validate_entry(session, project_id, entry_id, snapshot) -> None`。

- [ ] RED：`test_entry_binding_is_not_network_authorization` 校验有效 Target、同源、精确端口，修改 origin 后旧入口拒绝；项目保存不发送请求。同 Target 加入两个项目不共享整理信息。
- [ ] RED：`test_source_projection_preserves_unknown_without_protected_reads` 覆盖 scan/inventory、终态失败零观察、旧字段缺失、认证不确定子观察和候选；snapshot 不修改来源。无 target_id 的旧 quick 只允许入口 origin 能严格匹配时显式关联，快照保留原 target 缺失，不补写源任务。
- [ ] RED：`test_archived_entry_and_missing_source` 拒绝归档项目变更、禁用入口、running 历史入库、缺失任务及不匹配目标；旧来源当前无法证明绑定时给明确拒绝理由。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_projects.py tests/test_project_source.py -q` 观察失败；复用 AssetCatalogService 安全投影并实现服务，不重写采集。
- [ ] GREEN 同命令及 `tests/test_unified_assets.py`；提交。

### Task 3: 幂等预览、事务入库和持久观察

**Files:** 新增 `app/services/project_ingestion.py`、`tests/test_project_ingestion.py`，复用 Task 1/2。
**Interfaces:** `ProjectIngestionService.preview(session, project_id, request: ProjectImportRequest) -> ProjectImportPreview`；`confirm(session, project_id, preview_token: str) -> ProjectRun`；`sync(session, project_run_id: int) -> ProjectRun`。事务内主体 upsert、不可变观察/候选/覆盖快照、计数及 revision 一起提交；调用方不得拆分提交。

- [ ] RED：`test_preview_read_only_and_stale_confirmation` 证明预览不写库/目标，过期、Target 编辑、项目归档、源数据变化均拒绝确认；确认使用重算快照而非客户端计数。
- [ ] RED：`test_ingestion_idempotent_concurrent_and_atomic` 两个独立 DB session 并发同任务确认只生成一次观察；故障回滚无半批快照；重试/重启恢复；complete 后源摘要变化返回 source_changed。
- [ ] RED：`test_observation_history_never_collapses_identity_or_time` 同主体跨任务匿名 401/user 200 两观察，旧任务晚入库不回退最近时间；时间缺失 unknown；候选分离且可链接后来响应，跨项目独立；来源删除后快照仍可读。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_ingestion.py -q` 验证 RED；实现唯一键竞争处理和有界批量 SQL，错误消息无 payload。
- [ ] GREEN 同命令加 Task 1/2；提交。

### Task 4: 新任务关联、终态索引和中断恢复

**Files:** 新增 `app/services/project_lifecycle.py`、`tests/test_project_lifecycle.py`；修改 `app/schemas/assessment.py`、`identity.py`、`scan.py`、`app/services/scan_application.py`、`identity_collection.py`、`inventory.py`、`scan_submission_preview.py`、`identity_preview.py` 及对应测试。
**Interfaces:** 可选 `project_entry_id: int | None` 加入新任务请求并进入预览绑定；`ProjectLifecycleService.attach(session, entry_id, ref: ProjectSourceRef) -> ProjectRun`；`reconcile(ref: ProjectSourceRef) -> None` 自建 session，处理该任务的 pending/failed 关联；`retry(project_run_id: int) -> ProjectRun` 显式恢复。

- [ ] RED：`test_attach_before_and_after_inline_completion` 验证同步 dispatcher 先完成也能入库；去重返回已有任务仍建立关联；不选项目完全保持旧行为。
- [ ] RED：`test_terminal_index_failure_does_not_change_scan_or_report` 覆盖 quick/固定身份/identity/inventory 的完成、失败、取消、启动恢复中断；索引异常单独记 failed，原任务状态及报告字节不变。
- [ ] RED：`test_recovery_and_restart_require_valid_binding` importing 遗留可显式重试，归档／Target 变动拒绝；补采不默认继承项目，只有显式项目选择或历史入库才能关联。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_lifecycle.py -q` 观察失败；在真实编排终态提交后接 reconcile，attach 后也复查终态，所有数据库写入避开 ORM 事件中的额外提交。
- [ ] GREEN 同命令及`tests/test_url_scan_pipeline.py tests/test_identity_collection.py tests/test_inventory_services.py tests/test_scan_submission_preview.py`；提交。

### Task 5: 项目清单、历史和一致筛选

**Files:** 新增 `app/services/project_catalog.py`、`tests/test_project_catalog.py`。
**Interfaces:** `ProjectCatalogService.list(session, project_id, query: ProjectQuery) -> ProjectPage`、`detail(session, project_id, asset_id) -> ProjectAssetDetail`、`iter_rows(session, project_id, query) -> Iterator[dict]`（忽略分页，仅供一致事务内导出）。详情观察通过 view=observations 分页查询。

- [ ] RED：`test_sql_filtered_project_counts_and_history` 断言所有筛选、时间边界、稳定 ID 次级排序、默认 50/上限 200；观察条件作用于匹配观察后形成主体，主体／观察／候选口径明确；无匹配不把项目历史总量显示为零。
- [ ] RED：`test_latest_conflicts_and_deleted_source` 乱序、缺时间、同时间不同响应显示各自来源；原始观察内 confirmed 200/uncertain 401 不混；源任务删除仅标证据不可用。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_catalog.py -q` RED；实现 SQL 索引查询和历史视图，不反复加载所有源任务；为 Task 8 展示覆盖层预留纯查询入口。
- [ ] GREEN 同命令；提交。

### Task 6: 可比性驱动的项目变更

**Files:** 新增 `app/services/project_comparison.py`、`tests/test_project_comparison.py`。
**Interfaces:** `ProjectComparisonService.compare(session, project_id, before_project_run_id, after_project_run_id) -> ProjectComparison`，返回分入口/身份结果、原因和两端证据，状态仅使用 Global Constraints 枚举。

- [ ] RED：`test_project_changes_require_comparable_contexts` 参数化目标/入口/origin/模式/预算/来源规则变化、时间缺失、身份失败、旧 profile 不明、截断和补采；缺席一律 unknown，保留实际观察差异说明。
- [ ] RED：`test_200_to_404_is_response_change_not_disappearance` 完整同身份观察显示 attributes_changed；新增 newly_observed；完整同范围缺席 not_observed；同名不同账号不可相互替代。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_comparison.py -q` RED；只比较不可变来源快照，人工展示纠正不重写历史差异，不放宽旧 ScanComparisonService。
- [ ] GREEN 同命令与`tests/test_run_comparison.py tests/test_run_comparison_e2e.py`；提交。

### Task 7: 来源证据关系

**Files:** 新增 `app/services/project_relations.py`、`tests/test_project_relations.py`；连接 `project_ingestion.py`、`project_catalog.py`。
**Interfaces:** `ProjectRelationService.index(session, project_run_id) -> int` 幂等构建；`list(session, project_id, query: ProjectQuery) -> ProjectPage` 查询入边/出边及证据。关系自然键包含源任务、类型、端点引用和来源证据。

- [ ] RED：`test_relations_use_saved_sources_only` 页面引用、脚本声明、重定向分别保留；候选目标不升级成已请求；歧义端点留无法解析说明；同目录不得推断关系，重复入库不重复边。
- [ ] RED：`test_missing_relations_and_sensitive_sources` 旧任务缺数据返回未知限制而非零关系承诺；URL 查询脱敏、跨项目引用拒绝、无 protected 读取。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_relations.py -q` RED，后实现只读来源解析和事务内索引；GREEN 同命令；提交。

### Task 8: 标签备注与可撤销归并纠正

**Files:** 新增 `app/services/project_curation.py`、`tests/test_project_curation.py`；接入 `project_catalog.py`。
**Interfaces:** `annotate(session, project_id, asset_id, tags: list[str], note: str, expected_revision: int) -> ProjectAnnotation`；`preview(session, project_id, request: ProjectCorrectionRequest) -> ProjectCorrectionPreview`；`confirm(session, project_id, preview_token: str) -> ProjectCorrection`；`revoke(session, project_id, correction_id, expected_revision, reason) -> ProjectCorrection`。request 明确 merge/split、保留主体或选定观察、原因。

- [ ] RED：`test_merge_split_revoke_preserves_original_ids_and_observations` 合并保留别名，拆分只重分配选定观察，撤销恢复；标签附着原 ID，不丢失；新观察按自然键继续归属。
- [ ] RED：`test_correction_rejects_cross_origin_cycles_and_stale_edits` 跨项目/origin/方法/类型/规则、环、自合并、重叠成员、依赖撤销、并发入库和并发纠正使旧 revision 确认失败；审计追加且操作者不伪造。
- [ ] RED：`test_annotation_limits_and_safe_rendering` 20 项/40 字/2000 字上下边界、公式和 HTML、安全脱敏；归档项目保留读取，整理写操作拒绝以保持归档稳定。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_curation.py -q` RED；实现修正规则及有效展示归属索引，所有 revision 校验与写入同事务；GREEN 同命令与 catalog/comparison；提交。

### Task 9: 完整导出与万条观察基线

**Files:** 新增 `app/services/project_export.py`、`tests/test_project_export.py`、`tests/test_project_scale.py`。
**Interfaces:** `export_project(session, project_id, query: ProjectQuery, format: Literal['json','csv','markdown']) -> str`；JSON schema_version=`project-v1`，Markdown 仅概览，CSV 复用公式防护。

- [ ] RED：`test_project_exports_match_filtered_rows_not_page` 四类视图分别导出，页大小 50 不截断，未知值不变成零；JSON/CSV 计数和来源一致；敏感查询、原始指纹、状态文件路径不能出现。
- [ ] RED：`test_export_snapshot_with_concurrent_import_and_correction` 并发入库/纠正时导出来自同一读快照，无混合 revision、漏行或重复；必要时显式启动数据库读事务，不能假设 ORM 查询自动建立一致快照。
- [ ] RED：`test_ten_thousand_observations_paginate_and_export_exactly` 合成 10000 条，跨任务重复入库仍精确 10000；页间无重复，导出完整。记录环境、耗时、SQL 查询数，不设未经测量的性能宣传门槛。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_export.py tests/test_project_scale.py -q` RED，后实现分批查询的一致快照导出；GREEN；提交。

### Task 10: API 和 CLI 可操作闭环

**Files:** 新增 `app/api/routes/projects.py`、`app/cli/projects.py`、`tests/test_project_adapters.py`；修改 `app/api/router.py`、`app/cli/lazy_group.py`、现有扫描/身份/库存创建适配器传递 project_entry_id。
**Interfaces:** `/api/projects` 项目/入口 CRUD（无 delete）、`/{id}/imports/preview`、`/{id}/imports`、`/{id}/runs/{run_id}/sync`、`/{id}/assets`、`/{id}/assets/{asset_id}`、`/{id}/compare`、`/{id}/export`、`/{id}/annotations`、`/{id}/corrections/preview`、`/{id}/corrections`、`/{id}/corrections/{correction_id}/revoke`；CLI `garden projects create/list/show/archive/entry-add/entry-update/import/sync/assets/history/compare/relations/annotate/correct/revoke/export`。

- [ ] RED：`test_cli_api_share_project_contracts` 完整历史入库到纠正撤销/导出，相同错误码、计数、筛选；CLI import/correct 在无 --yes 时展示预览并确认；JSON 错误无 payload。
- [ ] RED：`test_project_mutations_reject_cross_origin_and_forged_ids` 复用现有 same-origin 防护，跨项目 IDs、过期 token、超长字段/请求被拒绝；GET 与 --help 不产生项目数据或目标请求。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_adapters.py -q` RED；薄适配器调用共用服务，CLI 无 mutable defaults；GREEN 同命令及`tests/test_cli.py tests/test_identity_adapters.py tests/test_scan_preview_pages.py`；提交。

### Task 11: 原生 Web 与真实用户旅程

**Files:** 新增 `app/api/routes/project_pages.py`，模板 `app/templates/projects/list.html`、`detail.html`、`assets.html`、`asset_detail.html`、`import_preview.html`、`comparison.html`、`correction_preview.html`；新增 `tests/test_project_journey_e2e.py`；修改 `app/api/router.py`、`app/templates/base.html`、`_scan_preview_form.html`、`identity_preview.html`、相关扫描表单与 `app/static/app.css`。
**Interfaces:** `/projects` 与 `/projects/{id}` 原生 GET 表单/POST 确认后重定向；项目入口启动链接只预填现有扫描流程，仍需原预览确认；查询参数可复制、刷新和导出。

- [ ] RED：`test_two_entry_project_history_curation_and_export_without_js` 真实本地两个 origin、多轮匿名/登录采集，自动关联与历史导入，同路由稳定主体/跨 origin 分离，查看变更/关系、标签、合并/拆分及撤销、下载验证；原任务报告字节不变。
- [ ] RED：`test_mobile_project_workflow_and_failed_identity_unknown` 390×844 窄屏与 1280×900 桌面截图；无 JS 表单完整操作、筛选刷新保留、长 URL/同名身份可辨认、失败身份不显示下线，陈旧纠正显示重新预览入口。
- [ ] 运行 `PYTHONPATH=. python -m pytest tests/test_project_journey_e2e.py -q` RED；最小页面实现并视觉检查；GREEN 后串行运行`tests/test_identity_journey_e2e.py tests/test_scan_preview_e2e.py`；提交。

### Task 12: 发布收尾、独立评审与交付

**Files:** 修改 `pyproject.toml`、`app/core/settings.py`、`README.md`、`docs/index.html`、版本／doctor 迁移断言；新增 `docs/demo-v07-project-assets.md`，更新本计划和设计状态。

- [ ] 逐项核对设计 §1–8：Tasks 1–11 均完成才设置 0.7.0；据 Task 11 实测写演示，说明历史范围、人工修正、未知和规模限制。
- [ ] Native 最终门请求一名新的独立只读评审者，覆盖范围 main `1529fe6` 到最终分支；重点审阅 Review Focus 五项。针对发现先固定失败场景再修复，记录裁定及成本；不自行派发实现子代理。
- [ ] 运行 `PYTHONPATH=. python -m pytest -o addopts='' -q`、`ruff check .`、`ruff format --check .`、`git diff --check`；检查实际退出码和汇总。浏览器测试串行，不把旧成绩冒充本次验证。
- [ ] 构建 wheel，核对 0009、项目模板/服务/CLI；隔离目录运行版本/help、旧库升级；记录环境、SHA、真实输出，未执行 Linux／干净依赖安装明确标注。
- [ ] 提交已验证代码文档，保留隔离工作区及用户原改动；报告完成范围、测试、裁定和限制。不推送、不创建 PR、不打 tag，等待后续独立指令。

## Self-review and Execution Handoff

映射：设计 §3→Tasks 1–4/10/11，§4→1/3/5，§5→6，§6→7/8，§7→5/9–12，§8→各片 RED 与最终回归。六项原路线能力都有实现任务；五项 Review Focus 均有对应测试。

接口自审：source ref、entry ID、ProjectRun ID 与原 run ID 明确分开；比较只接受同项目 ProjectRun IDs；query/export 共用 ProjectQuery；schema 在 Task 1 建立后随所属任务补足可序列化视图。Task 7 索引纳入 Task 3 同一入库事务；Task 8 展示覆盖只改变查询归属，Task 6 始终使用原始来源快照。

Native：本会话主代理按顺序实施全部 12 片，每片独立 RED/GREEN/提交，最终一名独立评审。暂不派发子代理；计划评审通过后开始实施，不再逐片请求普通实现许可。实施中；下列进度记录覆盖实际代码和本次验证，整版尚未验收。


## 2026-10-09 实施进度（未完成版本）

环境：macOS，本地 Python 3.10.10，隔离工作区 `.worktrees/project-assets-v07`，分支 `codex/project-assets-v07`，基线 `1529fe6`。未改主工作区原有文件，未推送或创建 PR。

- 已提交基础 `b183b77`：11 个项目数据表、0009 无损迁移/有数据拒绝降级、项目/入口绑定、只读来源预览、终态幂等入库与安全快照。提交后的补充测试发现 SQLite SAVEPOINT 需显式外层 BEGIN；修复在当前工作区，尚待下一提交。
- 当前代码已接入 quick/固定身份扫描共用启动流程、独立身份采集、可选项目库存采集；取消/启动中断后的扫描索引单独执行。索引失败不重写原任务状态或报告。库存选择项目时先持久化运行/关联，再单独提交终态并入库；未选项目保持原事务路径。
- 已实现并测试核心查询、来源删除后快照读取、按来源时间汇总与同时冲突保留；比较 200→404 与不完整覆盖；保存的页面/脚本关系；合并/拆分/撤销和新观察应用合并、原 ID 标签归属；JSON/CSV 全量导出与显式读事务。
- 测试证据：基础+迁移+doctor 64 项通过；随后项目/扫描管线/预览/assessment 回归 91 项通过；最新事务/生命周期/整理/导出针对性 17 项通过。不同组存在交集，不相加为唯一测试总数。真实命令均使用 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest <对应测试文件> -q`，本地输出在未入库的 `.verification/`。
- 合成规模 `tests/test_project_scale.py`：10000 条观察、101 个主体，重复确认不增记录，50 个 200 条分页无重复，JSON 精确导出 10000 条。首次本机基线：入库 7.706s；分页+导出 2.454s；这不是跨环境性能承诺，SQL 次数及并发快照实验仍待补充。
- 所有新行为已先观察缺失模块/断言失败或事务错误，再实现；纯扩展边界测试若已通过则保留，不人为制造红灯。曾运行 Ruff 全库 check 与 diff 检查通过，后续新文件仍需最终重跑。

未完成项（不能将 Tasks 1–12 或 v0.7 标记整体完成）：全部筛选视图一致性和并发试验、真实重定向关系、更多身份/配置比较边界、整理依赖/并发边界、固定身份及库存失败专项、API/CLI/Web 接线、无 JS 桌面/移动用户旅程、真实演示文档、全量 pytest/格式/打包和独立最终评审。版本号仍保持 0.6.0。

## 2026-10-10 继续实施与评审门状态

此节取代上一节的待办现状，不追溯勾选没有逐项证据的 RED/GREEN 复选框。项目六项核心能力、API、CLI 与原生 Web 已实现，检查点 `d47e4d3`。新增项目可选参数已穿过 quick、固定覆盖、身份采集和 inventory 适配器；0009、原始快照、展示整理、完整导出均已接入。

本轮已验证并修复：

- 身份健康缺失曾返回 unchanged：通过公开比较服务先得到失败断言，再要求登录身份有正面健康证据；匿名上下文无健康字段仍按原匿名语义处理。补测配置、时间、模式、补采不确定性。
- 两个 SQLite WAL 会话的陈旧确认曾抛 OperationalError：现在仅把 SQLite BUSY/LOCKED 转为 `project_concurrent_update`；回滚后重试幂等。其他 SQL 错误保持原异常。
- 原生表单冲突曾返回 HTTP 200：现在正确使用 400/404/409，同时保留 HTML 错误页。
- API/CLI/导出共用的关系限制补足“没有保存关系不代表不存在依赖”；重定向证据先按 asset 分组，避免逐资产扫描全部 evidence。
- 真实无 JS Web：双 origin 扫描、标签筛选刷新与下载、合并→拆分→逆序撤销、报告字节不变；登录验证码模拟→恢复验证→项目身份采集。桌面/移动截图已检查。3 个旅程 40.84s 通过，扩展整理旅程随后 9.11s 通过。
- 万条基线已补 SQL 计数：建立 fixture/预览/重复确认 10268 次，分页及导出 909 次；耗时 7.625s / 2.454s。保留为优化依据，无生产性能承诺。
- wheel/sdist 资源完整；sdist 原先包含 39 项本地验证产物，现已排除验证目录、截图、worktree 和 coverage。已安装包在隔离目录执行迁移和 CLI 通过，复用现有依赖。干净依赖下载受 SOCKS 支持缺失影响，未通过。
- README、展示页和 `docs/demo-v07-project-assets.md` 据实更新为开发预览。版本仍为 0.6.0；不创建 release/tag，不推送。

审查状态：Standards 与 Spec 两名只读审查者均因账户额度限制报错，未产生任何结论；不可标为独立审查通过。已做主代理自查与上述故障回归，但不替代独立审查。

验证注意：首次全量为 794 passed / 1 failed（旧字段集合断言，已修复）。一次使用显式 glob 的专项命令虽然带 `--ignore`，仍收集到浏览器文件，在受限环境启动 Chromium 失败；该次为 50 passed / 1 failed，不能记为通过。真实浏览器此前已单独通过，后续全量必须串行并使用允许浏览器的环境。完整原始输出留在本地 `.verification/`。

后续核销：中间全量 807 passed / 2 warnings，548.10s。此后完成去重晚关联终态复查（先固定 pending 错误）、inventory 失败快照验收、Markdown 筛选证据链接、空 CSV 元数据行、关系筛选严格关联自身来源观察或候选。多账号项目 Web 已补完：单/双账号三个参数组合 58.65s 全通过；双账号分别验证码登录与恢复验证，同角色、不同档案名，共享两个主体但保留四条观察。上述待办中对应项已解决。

仍需最终核销：独立审查、各视图排序/筛选的完整矩阵、最终整版全量及版本切换。当前不宣告整个 v0.7 完成。

补充核销：四视图筛选/排序/分页/JSON 全量导出矩阵 `tests/test_project_query_matrix.py` 4 项通过。登录观察缺少自身 identity_assessment 时的比较已先 RED 后改为 unknown；Target 变更后的入口新增公开 binding_status，并在 Web 提前显示重新绑定、隐藏启动链接。原校验继续拒绝旧绑定，项目不扩大范围。Standards/Spec 独立审查重试仍因额度失败，无任何审查结论。最终门因此仍未满足。
