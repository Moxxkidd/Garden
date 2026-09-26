# v0.4.0 Unified Asset List Implementation Plan

**Goal:** 让扫描与 inventory 的已记录资产共享 Web、CLI、JSON/CSV 清单。
**Architecture:** 只读投影服务统一来源与状态语义；所有消费端共用过滤、排序、分页和导出序列化。无新数据库表。
**Tech Stack:** Python 3.10+、SQLAlchemy、Pydantic、FastAPI/Jinja、Typer。
**Spec:** docs/superpowers/specs/2026-09-25-unified-assets-design.md

## Global Constraints

- source/run_id 必填查询范围；不跨身份或跨任务归并；保留未知与来源。
- 不触网、不打开秘密材料；投影后搜索；导出全量匹配行。
- 保留现有 API/数据库/采集流程；版本 0.4.0。

## Review Focus

- 同源不同查询值脱敏后不能误合并；不同上下文不合并。
- legacy 缺字段/非法 URL/状态码仍可阅读，不泄密、不崩溃。
- 参数越界或未知过滤值应明确拒绝；空结果与未知范围不同。
- 多页导出与过滤/排序一致，运行中标注计数可能变化。
- 输入含 HTML、Rich 标记或 CSV 公式不能触发执行。

## Task 1: 统一投影与导出

- [x] 写 tests/test_unified_assets.py：scan/inventory 计数、身份、证据、unknown、脱敏、分页及 CSV 防护。
- [x] 观察红灯；实现 app/schemas/assets.py、app/services/asset_catalog.py、app/services/asset_export.py。
- [x] 跑测试并确认只读边界。

## Task 2: Web/API 与 CLI

- [x] 写 tests/test_asset_surfaces.py：三端一致、导出全量、无效参数、输出文件保护。
- [x] 观察红灯；实现 app/api/routes/assets.py、app/cli/assets.py、app/templates/assets.html。
- [x] 注册路由/惰性命令，增加原任务及导航入口。
- [x] 验证分页和过滤链接保留条件，错误与空状态可读。

## Task 3: 文档、版本和交付验证

- [x] 更新 README、项目展示页、版本标识。
- [x] 真实浏览器验证清单、筛选、分页与导出；使用隔离数据，避免正式库。
- [x] Ruff + 完整 pytest；代码审查并修复；记录验证结果。

## 验证记录（2026-09-26）

- 最终完整回归：`PYTHONPATH=. python -m pytest -ra`，580 passed，170.16 秒；2 条既有 websockets 弃用警告。
- `ruff check .` 与 `ruff format --check .` 通过（233 个文件），`git diff --check` 通过。
- 新增浏览器测试覆盖启用/禁用 JavaScript 下的来源选择、类型筛选、刷新保留条件、翻页、来源追溯、跨页完整下载与 inventory 切换；浏览操作未访问目标站点。
- 隔离本地站点真实扫描得到 3 条资产记录，CLI JSON、JSON 导出、CSV 导出记录 ID 一致。桌面与 390px 窄屏视觉检查完成，页面无横向溢出。
- 代码审查发现的大写嵌入 URL 查询值泄露与 URL 控制字符问题已补回归并修复；浏览器测试发现的下拉框可访问名称问题已修复。
- 本次无需数据库迁移。主工作区原有修改保留，实现位于独立 `codex/unified-asset-list` 工作区。
