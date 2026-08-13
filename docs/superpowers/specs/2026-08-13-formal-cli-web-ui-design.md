# 正式 CLI 与自动 Web UI 设计

## 目标

Garden 安装后应像正常用户级 CLI 一样从任意目录运行，不要求激活 Python 虚拟环境。单条命令 `garden scan URL` 自动确保本机 Web UI 可用、提交扫描并在前台显示进度与可点击本地地址；不会自动打开浏览器。

## 命令兼容

保留所有既有 Typer 命令组和行为。正式入口新增为 `garden`，`gardenctl` 是同一应用的兼容入口。仅扩展现有 `scan` 命令：URL 位置参数为主入口，`--url` 保持兼容；二者同时出现时拒绝执行。

```bash
garden scan http://127.0.0.1:3000/
garden scan http://127.0.0.1:3000/ --detach
gardenctl scan --url http://127.0.0.1:3000/
garden stop
```

默认扫描保持前台。`Ctrl+C` 优先取消本次扫描并在本命令启动服务时停止该服务，安全收尾上限为 3 秒，退出码为 130。`--detach` 明确转为后台提交；`garden stop` 取消该实例全部活动扫描并停止 Web UI。

## 用户级安装和目录

`install.sh` 支持 macOS、Linux 和 WSL，要求 Python 3.10+，不使用 `sudo`。它把可替换运行环境安装到 `~/.local/share/garden/runtime/`，把两个轻量启动器放到 `~/.local/bin/garden` 与 `~/.local/bin/gardenctl`。PATH 不包含 `~/.local/bin` 时只打印当前 shell 的精确配置命令，不修改 shell 配置。

默认用户数据根是 `~/.garden`，可由 `GARDEN_HOME` 覆盖：

```text
$GARDEN_HOME/
├── config.env
├── garden.db
├── reports/
├── storage/
├── logs/web.log
└── runtime/server.json
```

正式启动器只读取 `$GARDEN_HOME/config.env`，不读取当前目录的 `.env`；当前 shell 环境变量优先。仓库开发模式保持读取项目 `.env`。

## 数据库规则

安装器首次安装和升级时备份 SQLite 数据库后运行现有 `garden db upgrade`。若 Garden 正在运行，安装器拒绝升级并提示先执行 `garden stop`。扫描命令不自动迁移；发现 schema 落后时 fail-closed 并提示 `garden db upgrade`。

## Web UI 运行规则

扫描先复用健康的本地 Garden UI。否则后台运行既有 FastAPI/Uvicorn：优先 `127.0.0.1:8000`，占用时由系统选择空闲端口，竞争最多重试 3 次；`--ui-port` 指定端口被占用时明确失败。启动最多等待 10 秒，日志写入 `$GARDEN_HOME/logs/web.log`，状态文件仅记录 PID、端口、启动时间和数据根。运行管理不添加令牌或新的公开认证接口。

CLI 通过既有 `POST /api/scans` 提交任务，并使用既有状态 API 显示前台进度。输出只展示首页、详情页、进度和报告路径。

## 取消与停止

新增幂等 `POST /api/scans/{id}/cancel`。`queued` 和 `running` 扫描收敛到 `interrupted`，保留已提交资产、失败和阶段记录，不生成伪造的完整报告。pipeline 在阶段和请求边界检查取消状态。应用启动时将异常退出遗留的活动扫描收敛为 `interrupted`。

`garden stop` 通过运行状态文件停止本地实例：先取消全部活动扫描，再停止 Uvicorn 并清理状态文件。状态文件失效时只清理过期记录，不尝试终止不确定的外部进程。

## 非目标

- 不重构既有命令分组、应用服务、八阶段 pipeline 或认证覆盖能力。
- 不自动打开浏览器、不增加 `--json`、`start`、`status` 或 `logs` 命令。
- 不增加自动更新、运行时令牌或 Windows 原生安装器。
- 不让 `scan` 隐式迁移数据库，也不开始认证覆盖任务 6。

## 验收

- 临时 HOME 下安装后，未激活 venv 也能从任意目录执行 `garden --version` 和 `gardenctl --version`。
- `garden scan URL` 自动启动/复用 UI，输出本地首页和扫描详情，默认前台；`--detach` 立即返回。
- URL 位置参数和 `--url` 均可用，混用被拒绝。
- Ctrl+C、取消 API 和 `garden stop` 都使活动扫描进入 `interrupted`，且不影响无关实例。
- 迁移、现有 quick 兼容入口、完整 pytest、Ruff、Docker build 与 GitHub CI 均通过。
