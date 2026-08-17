# Garden

> 提交一个入口 URL，自动完成资产发现、证据归纳和报告生成。

[garden-ctl.com](https://garden-ctl.com/) · [架构说明](docs/architecture.md) · [部署文档](docs/deployment.md)

Garden 是一个面向已授权目标的被动资产扫描与报告系统。用户不需要手动串联登录、inventory、checks 或 report 命令；Web、HTTP API 和 CLI 都调用同一个核心应用服务。

```text
URL
  → 输入与网络校验
  → 同源目标发现
  → 资产与证据收集
  → 结果标准化
  → 被动分析
  → Markdown 报告
```

## 正式 CLI 快速开始

macOS、Linux 或 WSL（Python 3.10+）可直接在仓库根目录安装用户级 CLI；不需要 `sudo`，也不需要手动激活虚拟环境：

```bash
./install.sh
garden --version
garden scan http://127.0.0.1:13000/
```

首次运行会自动启动或复用仅监听本机回环地址的 Web UI，并输出首页和本次扫描详情地址；默认前台显示进度，但不会自动打开浏览器。用户数据默认保存于 `~/.garden`，可用 `GARDEN_HOME` 覆盖。若安装后找不到命令，请按安装器输出将 `~/.local/bin` 加入 PATH。

```bash
garden scan http://127.0.0.1:13000/ --detach  # 仅提交，立即返回
garden stop                                   # 中断活动扫描并停止本机 UI
```

`gardenctl` 是兼容别名，既有命令组和 `gardenctl scan --url URL` 均继续可用。

## 仓库开发快速开始

环境要求：Python 3.10+。

```bash
git clone https://github.com/Moxxkidd/Garden.git
cd Garden

python3 -m venv .venv
source .venv/bin/activate
make install

cp .env.example .env
make dev
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，输入一个已授权的 HTTP/HTTPS URL，然后点击 **Start scan**。

页面会自动展示：

- 当前阶段和真实进度
- 已发现的资产、证据和关注项数量
- 重试、部分失败和未覆盖原因
- 最终报告的在线阅读与下载入口

使用 Docker：

```bash
cp .env.example .env
docker compose up --build
```

## 一次提交的三种入口

### Web

访问 `http://127.0.0.1:8000/`，只需填写 URL。后续流程由系统自动执行。

### CLI

```bash
garden scan http://127.0.0.1:13000/
gardenctl scan --url http://127.0.0.1:13000/  # 兼容写法
```

可以用边界参数控制扫描规模：

```bash
garden scan http://127.0.0.1:13000/ \
  --max-pages 50 \
  --max-resources 200 \
  --max-depth 2 \
  --request-timeout 5 \
  --overall-timeout 90 \
  --retries 1
```

### HTTP API

```bash
curl -X POST http://127.0.0.1:8000/api/scans \
  -H 'content-type: application/json' \
  -d '{"url":"http://127.0.0.1:13000/"}'
```

相关接口：

- `POST /api/scans`：提交 URL
- `GET /api/scans/{id}`：读取进度、阶段和失败信息
- `POST /api/scans/{id}/cancel`：中断尚未结束的扫描并保留已写入结果
- `GET /api/scans/{id}/report`：阅读或下载报告

## 报告内容

报告由持久化的结构化数据生成，不解析或拼接 CLI 日志，包含：

- 执行摘要与扫描范围
- 发现的资产及关键属性
- `page`、`stylesheet`、`script`、`image`、`document` 分类资产
- 来源明确且默认脱敏的证据；静态资源使用大小、SHA-256、版本线索和安全信号摘要
- 已观察到的正向安全控制，例如 HSTS 和 X-Frame-Options
- 风险或关注项
- 阶段完成情况和扫描覆盖范围
- 分开列示的覆盖告警与请求失败；覆盖告警包含候选数、已请求数、未覆盖分类、命中限制和代表样本
- 报告生成时间

默认输出目录：

```text
exports/scan-reports/scan-<id>.md
```

## 安全边界

Garden 只应用于已获得授权的目标，并采用以下有界策略：

- 仅允许 `http` 和 `https`
- 只执行有界、同源的被动 `GET` 请求
- 每次连接和重定向都会重新校验目标地址
- 默认允许已授权的公网目标和本机回环目标
- 如需恢复仅本机模式，可显式设置 `GARDEN_ALLOW_NON_LOCAL_TARGETS=false`
- RFC1918/ULA 内网目标还需要设置 `GARDEN_ALLOW_PRIVATE_TARGETS=true`
- link-local、云元数据常用地址、组播和未指定地址始终拒绝
- 请求超时、整体超时、并发、重试、页面数、资源数和深度均有限制
- 页面与静态资源使用独立预算，默认优先采集最多 50 个 HTML 页面，再采集最多 200 个静态资源
- 非文本响应只记录类型和大小，不把二进制内容写入报告

## 核心架构

```text
Web / API / CLI
       ↓
ScanApplicationService.start_scan(url, options)
       ↓
Persisted ScanRun + six-stage ScanPipeline
       ↓
Assets / Evidence / Findings / Failures
       ↓
ScanReportService
```

CLI、路由和页面模板只是适配层，核心业务流程位于应用服务和流水线中。单个阶段或页面失败会被持久化并显示在任务和报告中，不会静默伪装成完整结果。

需要登录态、人工 triage、retest 或高级证据生命周期时，原有高级工作流仍然可用，但它们不是 URL 自动扫描的前置步骤。迁移说明见 [docs/legacy-cli-migration.md](docs/legacy-cli-migration.md)。

## 测试

```bash
make test
make lint
```

端到端测试脚本：

```bash
.venv/bin/python scripts/e2e_url_scan.py
```

## 文档

- [架构与执行链路](docs/architecture.md)
- [安装与部署](docs/deployment.md)
- [威胁模型与网络策略](docs/threat-model.md)
- [旧 CLI 迁移说明](docs/legacy-cli-migration.md)
- [重构验收清单](docs/url-scan-refactor-checklist.md)

## License

Garden 当前未声明开源许可证。复用或分发前请先联系仓库所有者。
