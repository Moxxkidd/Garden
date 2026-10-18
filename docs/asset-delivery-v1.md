# v0.9.0 资产交付契约

从项目清单点击「交付当前筛选清单」。筛选会带入交付预览，下载 JSON/CSV 包含全部匹配项，不限于屏幕页；Markdown/HTML 只提供概览和原服务证据入口。项目比较页可直接交付所选两次运行的变更。

## 使用

```bash
garden projects deliver 1 --dataset observations --filters '{"method":"GET"}'
garden projects deliver 1 --dataset variants --format csv
garden projects deliver 1 --dataset changes --before-run-id 2 --after-run-id 3
garden projects deliver 1 --dataset observations --format openapi
garden projects deliver 1 --page --filters '{"page_size":100}'
# 将上一页的 next_cursor 原样传入，并保持筛选和页大小不变
garden projects deliver 1 --page --filters '{"page_size":100}' --cursor 'CURSOR'
```

API：`GET /api/projects/{id}/delivery` 读取一页；`GET /api/projects/{id}/delivery/export?dataset=observations&format=json` 下载完整结果；`GET /api/delivery/schema` 返回 JSON Schema（2020-12）。身份/站点/标签/时间/关键词等查询字段沿用项目清单。`dataset` 可为 assets、observations、variants、candidates、relations、changes。变更数据集仅接受 before_run_id / after_run_id，不混用资产筛选；不完整覆盖仍为 unknown，不改成已删除。

## 稳定格式与分页

顶层 `schema_version=delivery-v1`，包含 project_id、revision、dataset、filters、matched、exported_count、next_cursor、items、source_coverage、coverage_summary、limitations。`items` 使用 record_type 区分六种记录，精确结构见 Schema。

- assets：稳定项目主体 ID、脱敏 URL、方法、类型、观察时间、观察数与整理注释。
- observations：不可变观察 ID、原始／展示主体、来源运行和身份、已保存的 AssetRecord。
- variants：观察与来源变体的关联；variant 内保留来源请求 ID 与身份内归并状态，不含指纹或私有请求。一个变体可能关联多条观察，关联行数不能当独立变体数；仅在同一来源任务和身份内识别，不推断跨任务等价。
- candidates：未请求线索，不能计作已观察资产。
- relations：已保存的发现或重定向关系；detail 为可扩展证据对象。
- changes：状态、原因、前后观察 ID，保留 unknown / not_observed 的区别。

同一主版本只增加可选字段，消费者应忽略未识别字段；删字段、改变类型或语义必须使用新主版本。null 表示未记录或未知，不能当零。原 project-v1 导出接口不变。

分页首轮不传 cursor；继续读取 next_cursor，直到 null。游标绑定项目修订、数据集、全部筛选和页大小，项目入库或整理变化后返回 409 `delivery_revision_changed`，应丢弃已累计的旧页并从头读取；不是服务器长期快照。完整下载在一个数据库读取快照中完成。完整下载和当前分页投影会在内存组织筛选结果，本版不承诺无限规模或流式文件交付。

CSV 每行带 schema、范围与覆盖摘要，record 列是对应类型的 JSON；空结果保留一条 row_kind=metadata。这样保留嵌套身份／证据和 null，不将复杂字段拆成丢失语义的平面表。使用电子表格打开时沿用已有公式注入防护。

## 请求变体与旧数据

v0.9 新入库的项目运行冻结现有公开请求归并结果，之后删除来源任务也不丢失变体投影。旧项目快照没有这些信息时输出 identity_status=unknown、variant=null，不读取私有原始请求补猜、不自动重建历史数据。无数据库迁移，现有 0011 保持不变。

## OpenAPI 草稿

输出采用 [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)，仅选择已观察且类型为 endpoint 的记录。保留字面路径、HTTP 方法、观察到的响应码；同路径不同 origin 用操作 servers 和逐观察来源明确区分。查询参数只有名称，类型与必填性未知；不推断正文、认证方案或响应 schema。Hash 路由和无法表示的字面大括号路径不伪装成接口路径，排除数量明确列出。

`x-garden-inferred`、`x-garden-missing-fields` 和 `x-garden-observations` 表明证据和缺口。缺失 security 不表示无需登录，HTTP 200 不代表业务有效。必须人工审核后才能用于其他系统；导出本身不会发起请求。

## 保护与限制

普通交付只读取已脱敏投影，不打开 Cookie、会话文件或受保护请求材料，不公开指纹。HTML 概要转义文本且不含脚本；证据链接相对于原 Garden 地址，离线阅读不能保证链接仍有效。没有外部连接器、Webhook、自动上传或自动执行 OpenAPI 的能力。


## 本版实测

2026-10-18 在独立合成项目上验证：禁用 JavaScript、390px 页面提交身份观察筛选并下载 JSON，预览和文件均为 2 条观察，敏感查询值仅显示 `[REDACTED]`。版本冻结后完整回归 915 项一次通过，另有 Python 3.12 成品安装、真实 loopback HTTP 采集入库及 API/CLI/Web 交付一致性冒烟。Linux CI 尚未执行，版本尚未发布。
