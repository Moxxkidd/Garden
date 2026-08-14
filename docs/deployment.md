# Garden 部署步骤

## 说明

这份文档覆盖从全新环境安装到“一次提交 URL，自动得到报告”的完整运行方式：

- 本地开发部署
- Docker 方式运行
- 与 DVWA 联调的测试步骤

你可以在这份文件基础上继续删改，调整成最终想要的发布说明。

## 一、环境要求

建议环境：

- Python 3.10+
- `pip`
- `venv`
- Docker

可选但推荐：

- `make`
- Chromium / Playwright 运行时

## 二、用户级正式 CLI（推荐）

适用于 macOS、Linux 和 WSL，要求 Python 3.10+。在仓库根目录执行：

```bash
./install.sh
garden --version
garden scan http://127.0.0.1:8080/
```

安装器不使用 `sudo`，也不会修改 shell 配置。运行环境位于 `~/.local/share/garden/runtime/`，命令入口是 `~/.local/bin/garden` 与兼容入口 `~/.local/bin/gardenctl`。若 `~/.local/bin` 不在 PATH，按安装器输出的命令配置后重新打开终端。

若当前 shell 设置了 SOCKS `ALL_PROXY`，安装器会在创建隔离运行环境时临时忽略该变量，并增加依赖下载重试；不会修改当前 shell 或其他项目的代理配置。

用户数据默认位于 `~/.garden`，可通过 `GARDEN_HOME` 覆盖：

```text
~/.garden/
├── config.env
├── garden.db
├── reports/
├── storage/
├── logs/web.log
└── runtime/server.json
```

`garden scan URL` 自动启动或复用仅监听 `127.0.0.1` 的 Web UI，终端会输出首页和本次详情地址。默认前台等待并显示进度；`--detach` 仅提交任务并立即返回。不要在同一条命令中同时使用位置 URL 和 `--url`。

```bash
garden scan http://127.0.0.1:8080/ --detach
garden stop
```

`garden stop` 会把全部 queued/running 扫描标记为 `interrupted` 并停止本机 UI，已写入的资产和证据会保留。安装器升级前会自动备份 SQLite 数据库；如果服务仍在运行，先执行 `garden stop`。正式 CLI 只读取 `$GARDEN_HOME/config.env`，不读取当前目录 `.env`，而当前 shell 环境变量优先。

## 三、本地开发部署

### 1. 克隆仓库

```bash
git clone <your-repo-url>
cd Garden
```

### 2. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
.venv/bin/pip install -e '.[dev]'
```

如果仓库里已经配置了 `Makefile`，也可以直接：

```bash
make install
```

### 4. 准备环境变量

复制配置文件：

```bash
cp .env.example .env
```

本地最常用的最小配置可以保留默认值，也可以显式设置：

```bash
export GARDEN_DATABASE_URL=sqlite+pysqlite:///./data/garden.db
export GARDEN_ALLOW_NON_LOCAL_TARGETS=false
export GARDEN_DEMO_ADMIN_PASSWORD=demo-admin-password
export GARDEN_DEMO_USER_PASSWORD=demo-user-password
```

### 5. 启动服务

方式一，直接使用 Make：

```bash
make demo
```

方式二，直接启动 FastAPI：

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 6. 验证服务

```bash
curl -s http://127.0.0.1:8000/healthz
```

浏览器打开：

- `http://127.0.0.1:8000/`

首页输入一个已授权 URL 后即可查看六阶段真实进度，并在完成后直接阅读或下载报告。

也可以只调用一次 HTTP 接口：

```bash
curl -s -X POST http://127.0.0.1:8000/api/scans \
  -H 'content-type: application/json' \
  -d '{"url":"http://127.0.0.1:8080/"}'
```

响应中的 `id` 可用于 Web/API 查看进度；不需要执行 inventory、checks 或 report 命令。

## 四、CLI 基础验证

激活虚拟环境后执行：

```bash
garden --help
garden version
garden healthcheck
garden scan http://127.0.0.1:8080/
```

## 五、Docker 方式运行

### 1. 构建镜像

```bash
docker build -t garden:local .
```

### 2. 运行容器

```bash
docker run --rm -it \
  -p 8000:8000 \
  -v "$(pwd)/exports:/app/exports" \
  -e GARDEN_DATABASE_URL=sqlite+pysqlite:///./data/garden.db \
  -e GARDEN_ALLOW_NON_LOCAL_TARGETS=false \
  -e GARDEN_DEMO_ADMIN_PASSWORD=demo-admin-password \
  -e GARDEN_DEMO_USER_PASSWORD=demo-user-password \
  garden:local
```

如果你希望持久化 `data/`：

```bash
docker run --rm -it \
  -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  -e GARDEN_DATABASE_URL=sqlite+pysqlite:///./data/garden.db \
  garden:local
```

### 3. 使用 docker compose

如果你想直接使用仓库里的 compose：

```bash
docker compose up --build
```

## 六、旧版登录后高级工作流（可选）

下面这些命令只用于需要登录态、人工 triage 或 retest 的高级流程；它们不是 URL 自动扫描的前置或后置步骤。

### 1. 导入 target

```bash
gardenctl target import --file examples/targets/local-demo-targets.yaml --on-duplicate skip
```

### 2. 添加 credential profile

```bash
gardenctl cred add \
  --target-id 1 \
  --name garden-demo-admin \
  --role admin \
  --auth-type password \
  --username admin@example.local \
  --secret-ref env://GARDEN_DEMO_ADMIN_PASSWORD \
  --login-config-path examples/login/demo-http-admin.yaml
```

### 3. 登录

```bash
gardenctl login test --target garden-demo-web --profile garden-demo-admin
```

### 4. 构建 inventory

```bash
gardenctl inventory build --target garden-demo-web --profile garden-demo-admin
```

### 5. 运行 checks

```bash
gardenctl checks run --inventory 1
```

### 6. 查看 findings / evidence

```bash
gardenctl findings list
gardenctl evidence list
```

## 七、DVWA 联调步骤

如果你想拿一个真实登录后靶场做演示，可以使用 DVWA。

### 1. 启动 DVWA

```bash
docker run --rm -d \
  --name dvwa \
  -p 8081:80 \
  vulnerables/web-dvwa
```

### 2. 初始化 DVWA

浏览器打开：

- `http://127.0.0.1:8081/setup.php`

点击：

- `Create / Reset Database`

然后登录：

- 用户名：`admin`
- 密码：`password`

登录后进入：

- `DVWA Security`

把安全级别调成：

- `Low`

### 3. 安装 Playwright 浏览器

```bash
.venv/bin/playwright install chromium
```

### 4. 准备 Garden 环境变量

```bash
export GARDEN_DATABASE_URL=sqlite+pysqlite:///./data/dvwa-test.db
export DVWA_ADMIN_PASSWORD=password
```

### 5. 添加 DVWA target

```bash
gardenctl target add \
  --name dvwa-local \
  --base-url http://127.0.0.1:8081 \
  --type web \
  --owner appsec \
  --tag demo \
  --tag dvwa \
  --tag local
```

### 6. 添加 Playwright 登录 profile

```bash
gardenctl cred add \
  --target-id 1 \
  --name dvwa-admin-ui \
  --role admin \
  --auth-type password \
  --username admin \
  --secret-ref env://DVWA_ADMIN_PASSWORD \
  --login-config-path examples/login/dvwa-playwright-admin.yaml
```

### 7. 测试登录

```bash
gardenctl login test --target dvwa-local --profile dvwa-admin-ui
```

### 8. 构建更聚焦的 inventory

```bash
gardenctl inventory build \
  --target dvwa-local \
  --profile dvwa-admin-ui \
  --include /index.php \
  --include /security.php \
  --include /vulnerabilities \
  --include /phpinfo.php \
  --exclude /setup.php \
  --exclude /docs
```

### 9. 运行 checks

```bash
gardenctl checks run --inventory <latest_inventory_id>
```

### 10. 查看结果

```bash
gardenctl findings list
gardenctl findings show <id>
gardenctl evidence list
gardenctl evidence show <id>
```

## 七、常用验证命令

```bash
make test
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

## 八、常见问题

### 1. `gardenctl: command not found`

先进入仓库并激活虚拟环境：

```bash
cd /path/to/Garden
source .venv/bin/activate
```

或者直接使用：

```bash
./.venv/bin/gardenctl --help
```

### 2. 非本地 target 被拒绝

Garden 默认只允许本地目标。  
如果确实是在授权环境中测试，才显式放开：

```bash
export GARDEN_ALLOW_NON_LOCAL_TARGETS=true
```

公网域名只需要上述开关；RFC1918/ULA 内网目标还需要：

```bash
export GARDEN_ALLOW_PRIVATE_TARGETS=true
```

link-local/云元数据、组播和未指定地址始终拒绝。默认每个请求首次失败后最多重试一次，且仅限连接/读取瞬断或 502/503/504。可通过 `GARDEN_SCAN_RETRY_ATTEMPTS=0` 完全关闭重试。

### 3. Playwright 无法运行

先安装浏览器运行时：

```bash
.venv/bin/playwright install chromium
```

### 4. 登录成功但 inventory 结果太少

优先检查：

- 登录配置是否真的拿到已登录态
- 是否需要 Playwright 而不是 HTTP 登录
- 是否把 `setup.php`、下载文档等无关路径也算进了爬取范围
- 是否需要通过 `--include` / `--exclude` 收窄范围

## 九、建议保留与不建议提交的文件

一般不建议把这些本地产物一起提交：

- `.venv/`
- `__pycache__/`
- `.pytest_cache/`
- `.ruff_cache/`
- `data/*.db`
- `data/session_payloads/`
- `data/evidence/`
- `exports/`

这些删除后不会影响项目源码本身，但会丢失本地运行状态和测试结果。
