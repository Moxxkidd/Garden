# CI 修复设计

## 背景

合并提交 `3069fce` 的 GitHub Actions 同时暴露了两个交付问题：Ruff 全仓格式检查因认证覆盖平台实施计划中的 Python 代码块未格式化而中止，Docker 镜像在安装项目 wheel 时因构建上下文缺少 `alembic.ini` 和 `migrations/` 而失败。因此 Python 3.10 和 3.12 的测试步骤没有实际执行，主分支保持红色。前两个问题修复后，首次完整执行的测试矩阵又暴露了第三个问题：迁移 wheel 测试使用 `--no-build-isolation`，但开发依赖没有安装 Hatchling 构建后端。

## 目标

- 保留 CI 对整个仓库执行 Ruff 格式检查的现有约束，并修正唯一不符合格式的文档。
- 让 Docker 构建上下文包含 Hatch wheel 强制打包所需的 Alembic 配置和迁移脚本。
- 让通过 `.[dev]` 建立的测试环境具备非隔离 wheel 构建所需的 Hatchling。
- 在本地重新验证 Ruff、完整测试、wheel 打包和 Docker 构建，并通过拉取请求触发 GitHub Actions。

## 非目标

- 不修改认证覆盖平台的业务逻辑、数据库模型或迁移内容。
- 不调整 CI 的测试矩阵、跳过规则或分支保护策略。
- 不开始实施认证覆盖平台任务 6。

## 方案

格式问题采用“修正文档”方案：继续保留 `ruff format --check .`，对现有实施计划执行 Ruff formatter。相比缩小检查目录或添加排除规则，这能维持单一本地与 CI 格式标准，不隐藏未来文档代码块中的格式问题。

Docker 问题采用“补全构建上下文”方案：在安装项目之前，将仓库根目录的 `alembic.ini` 和 `migrations/` 复制到镜像 `/app`。这与 `pyproject.toml` 中的 wheel `force-include` 来源路径一致，不改变运行时迁移资产的包内位置。

测试环境问题采用“显式开发依赖”方案：在 `dev` 可选依赖中加入与构建系统下限一致的 Hatchling。测试继续使用 `--no-build-isolation`，避免每次验证 wheel 时创建联网构建环境，同时让本地和 CI 的依赖契约一致。

## 验收标准

- `ruff check .` 和 `ruff format --check .` 均成功。
- 完整 pytest 套件成功；GitHub Actions 实际覆盖 Python 3.10 和 3.12。
- 本地 wheel 构建能够包含迁移资产。
- `docker build -t garden:ci .` 成功。
- 修复分支推送后，拉取请求的 CI、Docker build 和 CLI smoke 全部成功。

## 风险控制

本任务只修改 Dockerfile、开发依赖声明、格式化目标文档及本设计/计划文档。主目录的未提交文件不会被复制、暂存或提交；所有工作在独立 worktree 中完成。如果本地缺少某个 Python 版本或 Docker 服务，则以能够执行的本地检查为证据，并以 GitHub Actions 矩阵作为最终远端验证。
