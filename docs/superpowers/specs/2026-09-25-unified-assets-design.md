# v0.4.0 统一资产清单设计

## 目标与范围

按已确认路线图实现统一资产字段、查询、Web 表格、CLI 列表与 JSON/CSV 导出。复用扫描与 legacy inventory 的已持久化结果；本轮不修改采集、认证、归并规则，不建设跨任务资产库，无数据库迁移。

## 口径

- 一个查询显式绑定 source=scan|inventory 与 run_id；两种来源共享同一输出 schema。
- 一条输出对应一条已有 ScanAsset、InventoryPage 或 InventoryEndpoint；ID 带来源、任务、记录类型与数据库 ID，绝不使用脱敏 URL 去重。
- 扫描清单总数等于该任务已有 ScanAsset 数；inventory 总数等于 pages+endpoints。上下文之间不归并，也不将其宣称为独立业务资产数。
- 类型分类 page/endpoint/static/file/other，并保留原始类型。document 映射 file，未知类型归 other。
- 具有合法 HTTP 状态码仅代表 response_observed；缺失则 unknown，不推断安全、有效、失败、候选或资产不存在。401/403 仍是已观察响应。
- 身份来自关联上下文/凭据 role；旧匿名 quick 缺上下文可确认 anonymous，其他缺失身份 unknown。历史身份标签不代表当前登录态有效或实际角色验证通过。
- scope 包含执行状态、完整性说明、身份采集状态；清单不补造完整性，legacy inventory 的覆盖完整性统一 unknown。
- 只展示明确关联的扫描 evidence/request ID；inventory 旧数据没有逐项证据外键，不凭脱敏 URL 猜测关联。来源链接能回到原任务；缺发现链时明确未记录。

## 接口和交互

- GET /api/assets 与 /api/assets/export：source/run_id 必填，支持 kind、context、observation、q、sort；分页仅用于列表，导出包含全部匹配记录。
- GET /assets：无 scope 时显示任务选择入口；有 scope 时展示过滤表单、匹配数/总数、分页、排序、来源/身份/响应详情及 JSON/CSV 下载。
- garden assets list --source scan --run-id N，支持同样过滤、排序、分页与 --json。
- garden assets export --source scan --run-id N --format json|csv --output PATH；不静默覆盖现有文件。
- 结果页、inventory 详情、主导航与 CLI 扫描位置输出提供清单入口。既有扫描页面和原始导出接口保留。

## 数据与保护

只读取已有数据库，不请求目标、不读取 protected request/session 文件。每次查询只投影指定任务；当前有界扫描适合内存过滤和稳定排序，未来大规模资产库独立设计 SQL 索引与快照。

URL 所有查询值、userinfo、fragment 脱敏；标题等外部文本使用现有 RedactionService；不序列化 ORM/attributes 整包、cookie、密码、请求体、存储路径。Web 自动转义，CLI 按普通文本输出，CSV 对公式前缀转义。过滤基于脱敏投影，不能按敏感值探测命中。下载标记 no-store，CSV/JSON 用相同过滤集合，不静默截断。

运行中计数是读取时的结果，提示可能变化；不同时间请求不承诺一致快照。旧数据未知字段用 null/unknown，不补 0。

## 验收

1. scan 与 inventory 的三端总数/筛选结果/导出行数一致。
2. 分页稳定、重复脱敏 URL 不丢行、导出超过单页完整；搜索、过滤、排序组合与非法输入可验证。
3. 原始记录与上下文证据归属正确，缺失信息保持 unknown；401/403 不丢弃。
4. 请求敏感值不进入 JSON/CSV/HTML/CLI；公式/XSS/Rich markup 不执行。
5. 查询及导出不访问目标、不打开会话材料、不改写源记录。
6. 完整回归、Ruff、真实本地浏览器表格筛选/分页/下载检查。
