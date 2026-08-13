# 正式 CLI 与自动 Web UI 实施计划

> **供智能代理执行：** 必须按任务执行 RED、GREEN、回归验证和中文提交；实现方式为内联执行。

**目标：** 将 Garden 发布为可从任意目录运行的正式 CLI，并让现有 scan 自动管理本地 Web UI 与可取消的扫描生命周期。

**架构：** 新增轻量用户级启动器、路径/运行管理模块和最小取消接口；保留现有 Typer 命令、FastAPI API、ScanApplicationService 和八阶段 pipeline。CLI 通过本地 API 创建扫描，不再在 CLI 进程内同步执行 pipeline。

**技术栈：** Python 3.10+、Typer、FastAPI、Uvicorn、SQLAlchemy、Alembic、pytest、Ruff、POSIX shell。

## 全局约束

- 所有新增文档、日志和提交信息使用中文。
- 不更改既有命令分组，不增加 `start`、`status`、`logs` 或 `--json`。
- `gardenctl scan --url URL` 必须兼容；`garden scan URL` 是新主入口。
- 正式 CLI 用户数据默认在 `~/.garden`，仅由 `GARDEN_HOME` 覆盖。
- 本任务不进入认证覆盖任务 6，也不重构 ScanApplicationService 或 pipeline 阶段。

---

### 任务 1：路径和正式启动器

**文件：**

- 新建：`app/cli/paths.py`
- 新建：`install.sh`
- 修改：`app/core/settings.py`
- 新建：`tests/test_cli_paths.py`
- 新建：`tests/test_install_script.py`

- [ ] 写失败测试：正式模式从 `GARDEN_HOME` 解析配置、数据库、报告、日志和运行目录；安装器生成 `garden` 与 `gardenctl` 启动器且不覆盖用户数据。
- [ ] 运行定向测试，确认当前缺少路径/安装器实现而失败。
- [ ] 实现最小路径解析、正式启动模式与幂等安装器；首次安装创建数据目录、复制缺失配置、安装 runtime，升级前拒绝运行中的实例并执行备份/迁移。
- [ ] 重新运行定向测试并提交中文变更。

### 任务 2：本地 Web UI 运行管理

**文件：**

- 新建：`app/cli/web_runtime.py`
- 修改：`app/cli/main.py`
- 新建：`tests/test_web_runtime.py`

- [ ] 写失败测试：优先 8000、占用时空闲端口、显式端口冲突、状态复用、过期状态清理和 10 秒就绪失败。
- [ ] 运行定向测试确认 RED。
- [ ] 实现 Uvicorn 后台进程管理、状态文件、日志重定向和现有健康接口检查。
- [ ] 重新运行定向测试并提交中文变更。

### 任务 3：扫描取消与异常状态收敛

**文件：**

- 修改：`app/services/scan_application.py`
- 修改：`app/services/scan_pipeline.py`
- 修改：`app/api/routes/scans.py`
- 修改：`app/main.py`
- 新建：`tests/test_scan_cancellation.py`

- [ ] 写失败测试：活动扫描可取消、终态取消幂等、取消不生成报告、启动时遗留活动扫描收敛为 interrupted。
- [ ] 运行定向测试确认 RED。
- [ ] 在现有服务边界实现 cancel、pipeline 安全检查点、API 取消路由和启动收敛。
- [ ] 重新运行定向测试并提交中文变更。

### 任务 4：对齐现有 scan 与 garden stop

**文件：**

- 修改：`app/cli/scan.py`
- 修改：`app/cli/main.py`
- 新建：`app/cli/stop.py`
- 修改：`tests/test_quick_scan_cli.py`
- 新建：`tests/test_formal_scan_cli.py`

- [ ] 写失败测试：位置 URL、旧 `--url`、混用拒绝、`--detach`、API 提交、前台轮询、Ctrl+C 取消/退出码 130 和 stop 取消活动扫描。
- [ ] 运行定向测试确认 RED。
- [ ] 以本地 API 适配现有 scan，按规则输出地址和进度，增加最小 stop 命令。
- [ ] 重新运行定向测试并提交中文变更。

### 任务 5：中文文档、完整验收与交付

**文件：**

- 修改：`README.md`
- 修改：`docs/deployment.md`
- 修改：`docs/legacy-cli-migration.md`
- 修改：`docs/superpowers/specs/2026-08-13-formal-cli-web-ui-design.md`
- 修改：`docs/superpowers/plans/2026-08-13-formal-cli-web-ui.md`

- [ ] 更新中文安装、扫描、后台模式、停止和迁移说明。
- [ ] 在临时 HOME 和 GARDEN_HOME 运行真实安装/启动/扫描/停止 smoke。
- [ ] 运行完整 pytest、Ruff、wheel 资产验证、Docker build 和差异自审。
- [ ] 推送分支、创建草稿 PR、确认 GitHub Actions 的 Python 3.10、3.12、Docker 与 CLI smoke 成功。
