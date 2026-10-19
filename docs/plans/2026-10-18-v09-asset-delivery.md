# v0.9.0 资产交付实施与验收

基线 origin/main `6708e27`（v0.8 PR #38 已合并，四项 CI 成功）。隔离分支 codex/asset-delivery-v09，保留主工作区。依据桌面原路线 §9；本次不授权推送、PR 或发布。

## 范围与实现决定

复用项目库、筛选、比较与脱敏投影，不更改采集流程。新增 delivery-v1 JSON Schema、分层 JSON/CSV、修订绑定分页、变更清单、Markdown/HTML 概要及 OpenAPI 3.1.1 草稿。Web/CLI/API 共用服务；现有 project-v1 导出保持兼容。无新数据库表或运行依赖，无外部系统连接器。

1. 主体、请求变体、身份观察、候选、关系分别选择导出；请求变体保存已有归并投影，不暴露原请求/指纹，旧快照缺失明确 unknown。
2. 当前筛选导出全部匹配项，不限于屏幕页。分页响应携带总数与 next_cursor，绑定项目修订和全部查询；变更后 409 要求重读，不假装保留服务器快照。
3. 比较基于显式 before/after 项目运行；unknown/not_observed 保持原语义，不输出 deleted。
4. 草稿仅来自已有响应的 endpoint，按 origin/path/method 分开，查询参数仅名称，未知请求体/鉴权/响应 schema 不猜测。标记推断与缺失字段；Hash 页面不当 HTTP 接口。
5. 普通导出不打开私有材料，不加入 Cookie/Authorization/原始 URL 参数值；HTML 转义，CSV 复用现有公式防护。
6. schema 主版本固定；新增可选字段兼容，删改含义/类型必须新主版本，未知值保留 null；旧 export 不迁移。

## 有界验证计划

围绕公开服务先记录具体失败：分层和过滤完整性、项目变更的分页失效、脱敏/HTML 转义、草稿跨 origin 与未知语义。适配器覆盖一次 API/CLI/Web；使用同一合成项目贯穿。复用现有入库/比较/导出回归。开发时只跑相关测试，版本号先更新并检索全部旧断言，最终冻结后一次完整 pytest + Ruff/format/diff + 包资源与安装冒烟。无产品问题不重复全量、不追加万级性能实验，不引入未需求的防御抽象。

## 状态

本地实现与验收完成，版本 0.9.0；未推送、未创建 PR、未发布。


## 实施与证据（2026-10-18）

- 新增 `AssetDeliveryService` 与 delivery-v1 Pydantic/JSON Schema 契约，六类数据集和五种格式共用项目查询。标准分页绑定项目修订与筛选；完整导出使用已有 SQLite 读取快照。没有新表、迁移或运行依赖。
- 项目入库仅新增公开请求变体快照，复用 AssetCatalog 已有归并；不打开受保护请求文件、不输出指纹。旧项目无快照时显式 unknown。现有 project-v1 导出保持原接口。
- 新增 API `/api/projects/{id}/delivery`、`/delivery/export`、`/api/delivery/schema`；CLI `projects deliver`；项目清单/比较页直接进入交付。Web 为原生 GET 表单，无 JS 可用。
- OpenAPI 3.1.1 草稿依据官方规范 https://spec.openapis.org/oas/v3.1.1.html，保留观察和 origin，不推断请求体/鉴权/响应 schema，不将缺失 security 解释为匿名访问。

RED/GREEN：首次公开服务用例因服务不存在失败；实现后通过。请求变体第一次夹具误用 ScanRequest 字段导致 TypeError，不计产品 RED；改用公开 from_capture 后明确得到 unknown 而非 known（delivery-variants-valid-red.log）。冻结公开归并快照后，来源删除仍保留 known，去掉快照模拟旧记录则为 unknown。OpenAPI 缺模块 RED 后实现双 origin／敏感值消失／未知字段断言。

针对性命令 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -q -o addopts='' tests/test_asset_delivery.py tests/test_project_catalog.py tests/test_project_comparison.py tests/test_project_export.py tests/test_project_ingestion.py tests/test_cli.py tests/test_v030_release.py`：**42 passed / 14.54s**，日志 .verification/v09-focused.log。新增 5 个面向公开行为的测试，未新增采集器旅程或万级实验。版本号与两处发布断言在完整回归前一起改为 0.9.0。

真实 UI：独立合成项目、两个已入库观察，390×844、Chromium 145、页面 JavaScript 禁用。表单提交保留 dataset=observations/q=api，空可选字段没有引起校验错误；点击 JSON 下载实际得到 delivery-v1、2 条观察、筛选 q=api，敏感参数 synthetic-only 不在文件中。截图 output/playwright/v09-delivery-mobile.png 已人工查看，下载 output/playwright/garden-1-observations.json 已程序核对。旧 npm 临时缓存缺失与一次自动审批额度故障造成工具中断，重试后完成；未绕过权限。

打包：`python -m hatchling build -d .verification/package-v090` 成功，wheel 298 成员/sdist 500 成员，280 个应用文件及全部迁移资源逐字节核对；运行数据和 .verification/output/worktrees 未入包。哈希见 package-v090/inventory.json，最终文档收口后重建 sdist。

安装：在既有独立 Python 3.12.14 venv 使用 `pip install --no-deps --no-cache-dir --force-reinstall` 升级 0.9.0 wheel，不宣称本次重新全新安装依赖。从独立临时目录、空 PYTHONPATH 导入 site-packages，真实 loopback HTTP 只发送 1 次，项目导出 1 条观察；API/CLI 内容一致，Web 与 Schema 入口 200，`garden version` 为 Garden 0.9.0。日志 .verification/v09-installed-smoke.log。无新增浏览器采集行为，安装冒烟不额外启动浏览器。

冻结后完整命令：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -q -o addopts='' --basetemp=.verification/v09-full-temp`，日志 .verification/v09-full.log，已退出 0：915 passed / 2 warnings / 803.16s。两条为既有 websockets/uvicorn 弃用提示。Ruff check、370 文件格式检查、diff 与文档相对链接检查通过。最终收口前只等这轮结果，不修改产品代码或追加无关测试。


## 最终核销

| 原路线能力 | 实现与证据 |
|---|---|
| 版本化 schema / 兼容策略 | delivery-v1 判别记录模型、公开 Schema 入口、兼容策略文档；API 与模型验证通过。 |
| 分层交付 | 六种数据集；请求变体快照在来源删除后保留，旧快照 unknown；入库与历史查询回归通过。 |
| Web 筛选导出 | 清单带入筛选，原生表单与实际 JSON 下载 2 条观察，q=api 保留且敏感值不出现。 |
| 稳定分页 | next_cursor 绑定筛选、页大小和项目 revision；翻页不重复，修订变更 409，完整导出不按页截断。 |
| 变更清单 | 复用项目比较，显式前后运行、保留 unknown；CSV 全匹配项测试通过。 |
| 默认脱敏 | 沿用已有公开投影，无私有材料读取、无指纹导出；秘密值和指纹不在交付结果的断言通过。 |
| Markdown/HTML 概要 | 范围、计数、覆盖和相对证据入口；HTML 转义与 API/CLI/Web 一致性验证通过。 |
| OpenAPI 草稿 | 来源接口类型与已有响应门、操作 origin、查询名和状态码、推断标记及未知字段；双 origin 测试通过。 |

最终全量 **915 passed** 在版本号已改为 0.9.0 后运行，覆盖两处发布版本断言。本版新增 5 个行为测试，合计一次完整回归；没有为无关假设新增防御分支或扩大采集功能。当前环境 macOS / Python 3.10.10，全量浏览器串行；Python 3.12.14 成品安装升级冒烟通过。未运行本版 Linux CI，不能借用 v0.8 远端成功结果当作本版结果。

剩余限制：完整下载和当前分页投影在内存组织筛选结果，未做流式大型文件交付或新的性能承诺；旧快照变体不自动补建；OpenAPI 仍需人工审核。没有新 migration，数据库 head 保持 0011。下一步是用户授权后的推送与 PR，不自动执行。

### PR #39 CI 修正（2026-10-19）

提交 5890efb 的 GitHub Actions run 39147206620 在 Linux/Python 3.10 与 3.12 均因同一用例 `test_variants_survive_source_removal_and_legacy_is_unknown` 失败；CLI smoke 与 Docker build 通过。失败是夹具直接 `session.delete(run)` 时先删 scan_assets，仍存在 scan_requests 引用，触发 FOREIGN KEY constraint failed。请求到资产的 view-only ORM 关系不提供删除排序，当前项目也没有正式源扫描删除入口。

本地 macOS/Python 3.10 运行 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_asset_delivery.py::test_variants_survive_source_removal_and_legacy_is_unknown -q -o addopts=''` 复现同一外键错误；固定 PYTHONHASHSEED=0 的另一次进程却通过。这说明此前一次 915 passed 不能证明夹具的跨进程稳定性，并非仅 Linux 问题。

最小修正只调整该测试：先删除捕获请求并 flush，再删除源扫描，明确断言源扫描与请求确已消失，继续比较完整导出快照及旧记录 unknown 语义。不放宽外键、不跳过测试、不改产品删除语义。针对性命令 `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_asset_delivery.py tests/test_project_catalog.py tests/test_project_ingestion.py -q -o addopts='' --tb=short`：16 passed / 5.11s；Ruff check、format check（370 文件）、git diff --check 通过。修正后的完整 Linux 双版本回归交给 PR CI；本次未重复本地 13 分钟全量测试。
