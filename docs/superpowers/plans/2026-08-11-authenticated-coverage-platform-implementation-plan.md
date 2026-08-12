# 认证覆盖差异平台实施计划

> **供智能体执行：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐任务执行本计划。所有步骤使用复选框跟踪。

**目标：** 将 Garden 的 `ScanRun` 升级为统一验证运行，在保留单 URL quick scan 的同时，实现 anonymous、user、admin 三上下文覆盖差异和显式开启的跨角色主动权限重放。

**架构：** 继续以 `ScanApplicationService` 为唯一应用入口，在 `ScanRun` 下增加 `ScanContext`、`ScanRequest`、`CoverageDifference` 和 `ReplayExecution`。quick 与 authenticated-coverage 共用持久化阶段图；旧 `/api/scans` 与 `gardenctl scan` 作为 quick 兼容适配器，旧 `ScanJob`/inventory/evidence/finding 路径按迁移窗口逐步只读化和退役。

**技术栈：** Python 3.10+、FastAPI、Typer、SQLAlchemy 2.x、Alembic、Pydantic v2、httpx、Playwright、Jinja2、pytest、Ruff、SQLite（默认）/PostgreSQL（迁移兼容目标）。

## 全局约束

- 产品主线固定为“认证后应用面验证是核心，单 URL 被动扫描是低门槛入口”。
- 首版固定 `anonymous`、`user`、`admin` 三上下文，公共协议不开放任意 N 角色。
- 主动重放默认关闭，仅处理本次运行捕获的 `GET`、`HEAD`、`OPTIONS`，不修改路径、查询值或请求体。
- 允许的重放方向仅为 `user -> anonymous`、`admin -> user`、`admin -> anonymous`。
- 不完整上下文必须表示为 unknown/incomplete，不能解释为资产缺失或授权差异。
- 可复用会话材料不能进入普通数据库视图、日志、UI、CLI 或报告；展示路径统一脱敏。
- Web/API 主动重放必须经过操作者认证和权限校验；本地 CLI 必须显式传入授权确认。
- 保持 Python `>=3.10`，现有依赖上界策略不变；新增 Alembic 使用 `>=1.14.0,<2.0.0`。
- 所有新设计文档、实施计划、开发记录、验收记录和提交信息使用中文；代码标识、API、命令和标准协议名保留英文。
- 每个任务先写失败测试，最小实现后运行定向测试，再运行相关回归，最后单独提交。
- 每个任务提交后先用 `git show --name-status HEAD` 和 `git status --short` 确认范围，再执行 `git push origin "$(git branch --show-current)"`；禁止 force push，禁止把用户现有未关联改动带入提交。

---

## 文件结构锁定

### 新建文件

- `alembic.ini`：Alembic CLI 配置。
- `migrations/env.py`：从 Garden settings 读取数据库 URL，加载全部 ORM metadata。
- `migrations/script.py.mako`：迁移脚本模板。
- `migrations/versions/0001_existing_schema_baseline.py`：现有 schema 基线。
- `migrations/versions/0002_unified_assessment.py`：统一运行模型迁移。
- `migrations/versions/0003_migrate_legacy_workflow.py`：旧工作流数据迁入统一模型。
- `migrations/versions/0004_drop_legacy_workflow.py`：兼容窗口验收后移除旧工作流表。
- `app/models/scan_context.py`：`ScanContext`。
- `app/models/scan_request.py`：`ScanRequest`。
- `app/models/coverage_difference.py`：`CoverageDifference`。
- `app/models/replay_execution.py`：`ReplayExecution`。
- `app/schemas/assessment.py`：assessment 创建、查询、上下文、差异和重放协议。
- `app/services/context_establishment.py`：三上下文创建、凭证归属和会话有效性。
- `app/services/context_collection.py`：匿名/认证采集结果统一写入 `ScanAsset` 和 `ScanRequest`。
- `app/services/coverage_identity.py`：资产身份和稳定内容指纹。
- `app/services/coverage_comparison.py`：三上下文被动差异计算。
- `app/services/protected_payload_storage.py`：原子、严格权限的敏感重放载荷存储。
- `app/services/replay_policy.py`：候选方向、方法、同源和预算策略。
- `app/services/replay_sessions.py`：把目标 `AuthSession` 转成重放所需 cookie/header/storage state。
- `app/integrations/replay/base.py`：重放网关协议和响应类型。
- `app/integrations/replay/http_gateway.py`：httpx cookie/bearer 重放。
- `app/integrations/replay/playwright_gateway.py`：Playwright storage-state 重放。
- `app/services/authorization_replay.py`：候选执行、判定、证据和 finding 编排。
- `app/security/operator_auth.py`：操作者 token 校验和签名会话 cookie。
- `app/cli/database.py`：`gardenctl db upgrade/stamp-existing/current` 数据库命令。
- `app/api/routes/assessments.py`：规范 assessment API 和 Web 路由。
- `app/api/routes/operator_auth.py`：操作者登录/退出。
- `app/cli/assessments.py`：`gardenctl assess` 命令组。
- `app/templates/assessment_detail.html`：三上下文详情、覆盖矩阵和重放结果。
- `app/templates/operator_login.html`：本地/内部操作者登录页。
- `app/workers/assessment_worker.py`：数据库可恢复的单进程 worker。
- `tests/test_migrations.py`：fresh/legacy/rollback 迁移测试。
- `tests/test_assessment_models.py`：统一模型约束测试。
- `tests/test_assessment_application.py`：应用入口和 quick 兼容测试。
- `tests/test_context_establishment.py`：三上下文和会话测试。
- `tests/test_context_collection.py`：统一资产/请求写入测试。
- `tests/test_coverage_identity.py`：身份与动态内容规范化测试。
- `tests/test_coverage_comparison.py`：覆盖矩阵测试。
- `tests/test_replay_policy.py`：重放准入和安全边界测试。
- `tests/test_authorization_replay.py`：重放方向、响应判定、证据和 finding 测试。
- `tests/test_operator_auth.py`：Web/API 主动重放权限测试。
- `tests/test_assessment_api.py`：规范 API 和兼容 API 测试。
- `tests/test_assessment_cli.py`：规范 CLI 和兼容 CLI 测试。
- `tests/test_assessment_worker.py`：重启恢复、租约和陈旧 active key 测试。
- `tests/test_assessment_browser_e2e.py`：真实 Chromium 三角色端到端测试。
- `tests/test_legacy_removal.py`：兼容窗口退出门禁和旧表移除测试。
- `tests/helpers/assessment.py`：跨测试文件复用的 run/context/asset/request 工厂。

### 重点修改文件

- `pyproject.toml`：加入 Alembic，更新产品描述。
- `app/core/settings.py`：迁移、受保护存储、重放预算、操作者认证、demo gate 配置。
- `app/db/bootstrap.py`：移除生产路径 `create_all()`，提供迁移入口。
- `app/models/enums.py`、`app/models/__init__.py`、`app/models/scan_run.py`：统一状态、关系和 context-scoped 字段。
- `app/models/auth_session.py`、`app/models/audit_event.py`：关联统一运行/上下文和重放审计。
- `app/schemas/scan.py`：保留 quick 兼容协议并映射到 assessment。
- `app/services/scan_application.py`：增加 `start_assessment()`，`start_scan()` 成为兼容别名。
- `app/services/scan_pipeline.py`：替换为八阶段统一流水线。
- `app/services/scan_reporting.py`：统一报告。
- `app/api/routes/scans.py`、`app/api/router.py`：兼容映射和新路由装配。
- `app/cli/scan.py`、`app/cli/main.py`：quick alias 和 `assess` 命令装配。
- `app/templates/index.html`、`app/templates/scan_detail.html`：双入口与兼容跳转。
- `app/main.py`：worker 启停、恢复和 demo route gate。
- `.github/workflows/ci.yml`：迁移测试和真实浏览器 job。
- `README.md`、`PLANS.md`、`docs/architecture.md`、`docs/threat-model.md`、`docs/deployment.md`、`docs/demo-walkthrough.md`、`docs/legacy-cli-migration.md`：中文产品主线和迁移说明。

---

### 任务 1：引入 Alembic 并建立现有 schema 基线

**文件：**
- 新建：`alembic.ini`
- 新建：`migrations/env.py`
- 新建：`migrations/script.py.mako`
- 新建：`migrations/versions/0001_existing_schema_baseline.py`
- 新建：`tests/test_migrations.py`
- 新建：`app/cli/database.py`
- 修改：`pyproject.toml:15-35`
- 修改：`app/db/bootstrap.py:20-98`
- 修改：`app/cli/main.py:5-67`
- 修改：`app/core/settings.py:9-71`
- 修改：`app/main.py:17-29`
- 修改：`tests/conftest.py:153-165`

**接口：**
- 产出：`upgrade_database(database_url: str, revision: str = "head") -> None`
- 产出：`stamp_existing_database(database_url: str, revision: str = "0001") -> None`
- 产出：`gardenctl db upgrade|stamp-existing|current`
- 供任务 2 的 `0002_unified_assessment` 迁移使用。

- [ ] **步骤 1：写 fresh database 失败测试**

```python
def test_upgrade_database_creates_current_schema(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}"
    upgrade_database(url)
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"alembic_version", "scan_runs", "scan_jobs", "inventory_runs"} <= tables
```

- [ ] **步骤 2：写 legacy database stamp 失败测试**

```python
def test_stamp_existing_database_preserves_rows(legacy_database_url):
    stamp_existing_database(legacy_database_url)
    upgrade_database(legacy_database_url)
    with create_engine(legacy_database_url).connect() as connection:
        assert connection.scalar(text("select count(*) from targets")) == 1
        assert connection.scalar(text("select version_num from alembic_version")) == "0001"
```

- [ ] **步骤 3：运行迁移测试并确认失败**

运行：`.venv/bin/pytest tests/test_migrations.py -v`

预期：导入 `upgrade_database` 失败，且仓库不存在 Alembic 配置。

- [ ] **步骤 4：加入依赖和迁移入口**

在 `pyproject.toml` 运行依赖中加入：

```toml
"alembic>=1.14.0,<2.0.0",
```

在 `app/db/bootstrap.py` 增加：

```python
def upgrade_database(database_url: str, revision: str = "head") -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def stamp_existing_database(database_url: str, revision: str = "0001") -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, revision)
```

生产启动路径不再调用 `Base.metadata.create_all()`；测试 fixture 在 `init_database()` 后显式调用 `upgrade_database()`。

`GARDEN_DATABASE_AUTO_MIGRATE` 类型为 bool、默认 `false`，只允许本地开发显式开启。关闭时，应用发现 schema 不在 head 必须启动失败并提示执行 `gardenctl db upgrade`；生产环境发现该值为 `true` 时直接拒绝启动。已有业务表但没有 `alembic_version` 时必须要求显式执行 `gardenctl db stamp-existing`，不能自动猜测并 stamp。

- [ ] **步骤 5：编写 `0001` 基线并运行测试**

运行：`.venv/bin/pytest tests/test_migrations.py tests/test_models.py tests/test_inventory_models.py -v`

预期：全部通过，fresh 与已存在数据库均保留数据。

- [ ] **步骤 6：运行迁移往返验证**

运行：`.venv/bin/alembic upgrade head`

运行：`.venv/bin/alembic downgrade base`

运行：`.venv/bin/alembic upgrade head`

预期：三条命令退出码均为 0；仅在专用测试数据库上执行 downgrade。

- [ ] **步骤 7：提交**

```bash
git add pyproject.toml alembic.ini migrations app/db/bootstrap.py app/cli/database.py app/cli/main.py app/core/settings.py app/main.py tests/conftest.py tests/test_migrations.py
git commit -m "引入数据库迁移基线"
```

### 任务 2：建立统一运行模型和 assessment 协议

**文件：**
- 新建：`app/models/scan_context.py`
- 新建：`app/models/scan_request.py`
- 新建：`app/models/coverage_difference.py`
- 新建：`app/models/replay_execution.py`
- 新建：`app/schemas/assessment.py`
- 新建：`migrations/versions/0002_unified_assessment.py`
- 新建：`tests/test_assessment_models.py`
- 新建：`tests/helpers/assessment.py`
- 修改：`app/models/scan_run.py:15-132`
- 修改：`app/models/enums.py:27-123`
- 修改：`app/models/__init__.py:3-45`
- 修改：`app/models/auth_session.py:13-44`

**接口：**
- 产出：`AssessmentMode`, `ContextKind`, `ReplayVerdict`, `CompletenessStatus`
- 产出：`AssessmentStartRequest`, `AssessmentRunView`, `ScanContextView`, `CoverageDifferenceView`, `ReplayExecutionView`
- 产出 ORM：`ScanContext`, `ScanRequest`, `CoverageDifference`, `ReplayExecution`
- 产出测试工厂：`make_run()`, `make_authenticated_run()`, `add_asset()`, `add_request()`

- [ ] **步骤 1：写固定上下文约束测试**

```python
def test_authenticated_assessment_has_exactly_three_contexts(db_session):
    run = make_run(db_session, mode="authenticated_coverage")
    db_session.add_all(
        [
            ScanContext(scan_run_id=run.id, kind="anonymous", status="pending"),
            ScanContext(scan_run_id=run.id, kind="user", status="pending"),
            ScanContext(scan_run_id=run.id, kind="admin", status="pending"),
        ]
    )
    db_session.commit()
    assert [item.kind for item in run.contexts] == ["anonymous", "user", "admin"]
```

- [ ] **步骤 2：写重复 context 和非法 active 配置测试**

```python
def test_context_kind_is_unique_per_run(db_session):
    run = make_run(db_session)
    db_session.add_all(
        [
            ScanContext(scan_run_id=run.id, kind="user", status="pending"),
            ScanContext(scan_run_id=run.id, kind="user", status="pending"),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_active_replay_requires_authorization_confirmation():
    with pytest.raises(ValidationError):
        AssessmentStartRequest(
            url="http://127.0.0.1:8000",
            mode="authenticated_coverage",
            user_profile_id=1,
            admin_profile_id=2,
            active_checks_enabled=True,
        )
```

- [ ] **步骤 3：运行测试并确认模型不存在**

运行：`.venv/bin/pytest tests/test_assessment_models.py -v`

预期：新模型和 schema 导入失败。

- [ ] **步骤 4：实现枚举和 Pydantic 协议**

```python
class AssessmentMode(str, Enum):
    QUICK = "quick"
    AUTHENTICATED_COVERAGE = "authenticated_coverage"


class ContextKind(str, Enum):
    ANONYMOUS = "anonymous"
    USER = "user"
    ADMIN = "admin"


class ReplayVerdict(str, Enum):
    BLOCKED = "blocked"
    EQUIVALENT_ACCESS = "equivalent_access"
    CHANGED_RESPONSE = "changed_response"
    INCONCLUSIVE = "inconclusive"
```

`AssessmentStartRequest` 的 model validator 必须同时验证三条规则：quick 不接收 profile；authenticated-coverage 必须同时有 user/admin profile；active replay 必须有 `authorization_confirmed=True`。

`AssessmentRunView` 必须公开 `mode`、`completeness`、`active_checks_enabled`、`contexts`、`context_counts`、`difference_count` 和 `replay_count`。`context_counts` 类型固定为 `dict[str, int]`，键只允许 anonymous、user、admin。

- [ ] **步骤 5：实现 ORM 与数据库约束**

`ScanRun` 增加 `mode`、`target_id`、`source_run_id`、`active_checks_enabled`、`authorization_confirmed_at`、`authorization_confirmed_by`、`completeness`；`ScanAsset` 增加 nullable `context_id` 和 `identity_key`，随后由数据迁移回填。

`ScanContext` 使用：

```python
__table_args__ = (UniqueConstraint("scan_run_id", "kind"),)
```

`ScanRequest` 的普通字段只保存脱敏 URL 和 fingerprint，精确值只保存 `protected_storage_ref`。

- [ ] **步骤 6：编写 `0002` 并运行迁移与模型测试**

运行：`.venv/bin/pytest tests/test_migrations.py tests/test_assessment_models.py -v`

预期：fresh/legacy upgrade 通过，重复 context 被数据库拒绝。

- [ ] **步骤 7：提交**

```bash
git add app/models app/schemas/assessment.py migrations/versions/0002_unified_assessment.py tests/test_assessment_models.py tests/test_migrations.py
git commit -m "建立统一验证运行模型"
```

### 任务 3：统一应用入口并保持 quick 兼容

**文件：**
- 新建：`tests/test_assessment_application.py`
- 修改：`app/services/scan_application.py:31-254`
- 修改：`app/schemas/scan.py:11-86`
- 修改：`tests/test_url_scan_pipeline.py`
- 修改：`tests/test_quick_scan_cli.py`

**接口：**
- 产出：`ScanApplicationService.start_assessment(request: AssessmentStartRequest) -> AssessmentRunView`
- 保留：`start_scan(url: str, options: ScanOptions | None = None) -> ScanRunView`
- 产出：`create_assessment_stages(session: Session, run: ScanRun) -> None`

- [ ] **步骤 1：写 quick 兼容失败测试**

```python
def test_start_scan_delegates_to_quick_assessment(monkeypatch):
    service = build_inline_service(monkeypatch)
    view = service.start_scan("http://127.0.0.1:8080/")
    assert view.mode == "quick"
    assert [context.kind for context in view.contexts] == ["anonymous"]
```

- [ ] **步骤 2：写 authenticated-coverage 创建测试**

```python
def test_start_assessment_creates_three_contexts(assessment_profiles, inline_service):
    request = AssessmentStartRequest(
        url=assessment_profiles.url,
        mode="authenticated_coverage",
        user_profile_id=assessment_profiles.user_id,
        admin_profile_id=assessment_profiles.admin_id,
    )
    view = inline_service.start_assessment(request)
    assert [item.kind for item in view.contexts] == ["anonymous", "user", "admin"]
    assert [stage.name for stage in view.stages] == [
        "validate",
        "establish_contexts",
        "collect",
        "normalize",
        "compare_coverage",
        "replay_authorization",
        "analyze",
        "report",
    ]
```

- [ ] **步骤 3：写 quick 运行升级关联测试**

```python
def test_authenticated_assessment_can_reference_quick_source_run(
    inline_service, completed_quick_run, assessment_profiles
):
    view = inline_service.start_assessment(
        AssessmentStartRequest(
            url=completed_quick_run.url,
            mode="authenticated_coverage",
            source_run_id=completed_quick_run.id,
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )
    assert view.source_run_id == completed_quick_run.id
```

- [ ] **步骤 4：运行定向测试并确认失败**

运行：`.venv/bin/pytest tests/test_assessment_application.py tests/test_quick_scan_cli.py -v`

预期：`start_assessment` 和新视图字段不存在。

- [ ] **步骤 5：实现新入口和兼容适配**

```python
def start_scan(self, url: str, options: ScanOptions | None = None) -> ScanRunView:
    assessment = self.start_assessment(
        AssessmentStartRequest(url=url, mode=AssessmentMode.QUICK, options=options or ScanOptions())
    )
    return ScanRunView.model_validate(assessment.model_dump())
```

`active_key` 的计算必须包含 mode、profile IDs、active flag 和完整 options，完成/失败/incomplete/interrupted 时清空。

`source_run_id` 只接受同一 target 的已终态 quick run；它用于 UI/API 追踪“从低门槛入口升级到认证覆盖验证”的来源关系，不复制 quick 资产来伪造三上下文采集结果。

- [ ] **步骤 6：更新阶段图和终态等待逻辑**

quick 模式将 `compare_coverage` 和 `replay_authorization` 持久化为 `skipped`；authenticated-coverage 执行全部阶段。`wait_for_completion()` 增加 `incomplete` 和 `interrupted` 终态。

- [ ] **步骤 7：运行回归测试**

运行：`.venv/bin/pytest tests/test_assessment_application.py tests/test_url_scan_pipeline.py tests/test_quick_scan_cli.py -v`

预期：新测试和现有 quick 测试全部通过。

- [ ] **步骤 8：提交**

```bash
git add app/services/scan_application.py app/schemas/scan.py tests/test_assessment_application.py tests/test_url_scan_pipeline.py tests/test_quick_scan_cli.py
git commit -m "统一验证运行应用入口"
```

### 任务 4：建立三上下文并验证认证会话

**文件：**
- 新建：`app/services/context_establishment.py`
- 新建：`tests/test_context_establishment.py`
- 修改：`app/services/scan_pipeline.py:27-176`
- 修改：`app/services/sessions.py:34-246`
- 修改：`app/models/audit_event.py`
- 修改：`app/models/enums.py`

**接口：**
- 消费：`AssessmentStartRequest.user_profile_id/admin_profile_id`
- 产出：`ContextEstablishmentService.establish(session: Session, run: ScanRun) -> list[ScanContext]`
- 产出：`AuthSessionService.ensure_valid_for_profile(session: Session, profile_id: int) -> AuthSession`

- [ ] **步骤 1：写凭证归属和角色测试**

```python
def test_profiles_must_share_target_and_expected_roles(db_session, profiles):
    service = ContextEstablishmentService()
    run = make_authenticated_run(db_session, profiles.user_id, profiles.other_target_admin_id)
    with pytest.raises(InputValidationError, match="同一目标"):
        service.establish(db_session, run)
```

- [ ] **步骤 2：写会话失败导致 incomplete 测试**

```python
def test_admin_login_failure_marks_run_incomplete(db_session, failing_auth_service, profiles):
    run = make_authenticated_run(db_session, profiles.user_id, profiles.admin_id)
    service = ContextEstablishmentService(auth_service=failing_auth_service)
    contexts = service.establish(db_session, run)
    assert context_by_kind(contexts, "admin").status == "failed"
    assert run.status == "incomplete"
    assert run.completeness == "missing_admin_context"
```

- [ ] **步骤 3：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_context_establishment.py -v`

预期：服务和新 session helper 不存在。

- [ ] **步骤 4：实现上下文建立服务**

```python
class ContextEstablishmentService:
    def establish(self, session: Session, run: ScanRun) -> list[ScanContext]:
        anonymous = self._ensure_anonymous(session, run)
        if run.mode == AssessmentMode.QUICK.value:
            return [anonymous]
        user = self._ensure_authenticated(session, run, ContextKind.USER)
        admin = self._ensure_authenticated(session, run, ContextKind.ADMIN)
        return [anonymous, user, admin]
```

服务必须校验两个 profile 属于同一 target、角色分别为 user/admin，且 run URL 与 target base URL 同源。

- [ ] **步骤 5：将阶段接入 pipeline 并写审计**

新增 `CONTEXT_ESTABLISHMENT` 审计事件，记录 context kind、profile ID、session ID、成功/失败，不记录 secret 或 session payload。

- [ ] **步骤 6：运行认证回归**

运行：`.venv/bin/pytest tests/test_context_establishment.py tests/test_session_lifecycle.py tests/test_http_auth.py tests/test_playwright_contract.py -v`

预期：全部通过。

- [ ] **步骤 7：提交**

```bash
git add app/services/context_establishment.py app/services/scan_pipeline.py app/services/sessions.py app/models tests/test_context_establishment.py
git commit -m "建立三角色验证上下文"
```

### 任务 5：统一上下文采集、资产身份和请求捕获

**文件：**
- 新建：`app/services/context_collection.py`
- 新建：`app/services/coverage_identity.py`
- 新建：`tests/test_context_collection.py`
- 新建：`tests/test_coverage_identity.py`
- 修改：`app/services/scan_pipeline.py:188-365`
- 修改：`app/services/inventory_collection.py`
- 修改：`app/integrations/inventory/playwright_gateway.py`
- 修改：`app/services/session_storage.py`

**接口：**
- 产出：`canonical_asset_identity(method: str, url: str) -> str`
- 产出：`stable_response_signature(text: str, content_type: str | None) -> str`
- 产出：`ContextCollectionService.collect(session: Session, run: ScanRun, context: ScanContext) -> ContextCollectionSummary`
- 产出：`ObservedRequest`, `ObservedResource`, `ContextCollectionSummary`

- [ ] **步骤 1：写身份规范化测试**

```python
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://app/a?id=1&sort=name", "https://app/a?sort=date&id=9"),
        ("https://app:443/a/", "https://app/a"),
    ],
)
def test_asset_identity_ignores_values_and_default_port(left, right):
    assert canonical_asset_identity("GET", left) == canonical_asset_identity("GET", right)
```

- [ ] **步骤 2：写动态内容指纹测试**

```python
def test_signature_ignores_timestamp_nonce_and_trace_id():
    first = "generated=2026-08-11T01:02:03Z nonce=abc trace_id=req-1 stable=users"
    second = "generated=2026-08-11T02:03:04Z nonce=xyz trace_id=req-2 stable=users"
    assert stable_response_signature(first, "text/plain") == stable_response_signature(
        second, "text/plain"
    )
```

- [ ] **步骤 3：写 context-scoped 持久化测试**

```python
def test_collection_persists_assets_and_replayable_requests(db_session, fake_collection):
    summary = ContextCollectionService(gateway=fake_collection).collect(db_session, run, admin)
    assert summary.asset_count == 2
    assert all(asset.context_id == admin.id for asset in run.assets)
    assert {request.method for request in admin.requests} == {"GET"}
    assert all(request.protected_storage_ref for request in admin.requests)
```

- [ ] **步骤 4：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_coverage_identity.py tests/test_context_collection.py -v`

预期：新函数和服务不存在。

- [ ] **步骤 5：实现身份、指纹和采集适配**

匿名 context 复用 `HttpScanGateway`；认证 context 复用 Playwright inventory gateway。二者统一产出 `ObservedResource` 和 `ObservedRequest`，再由 `ContextCollectionService` 写入 `ScanAsset`/`ScanRequest`。

```python
@dataclass(frozen=True)
class ObservedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    status_code: int
    response_headers: dict[str, str]
    response_text: str


@dataclass(frozen=True)
class ObservedResource:
    asset_type: str
    method: str
    url: str
    status_code: int | None
    title: str | None
    attributes: dict[str, object]


@dataclass(frozen=True)
class ContextCollectionSummary:
    context_id: int
    asset_count: int
    request_count: int
    failure_count: int
```

- [ ] **步骤 6：接入 pipeline 并验证 quick 不回归**

运行：`.venv/bin/pytest tests/test_context_collection.py tests/test_coverage_identity.py tests/test_url_scan_pipeline.py tests/test_inventory_gateway.py -v`

预期：全部通过；quick 仍只生成 anonymous context 资产。

- [ ] **步骤 7：提交**

```bash
git add app/services/context_collection.py app/services/coverage_identity.py app/services/scan_pipeline.py app/services/inventory_collection.py app/integrations/inventory/playwright_gateway.py app/services/session_storage.py tests/test_context_collection.py tests/test_coverage_identity.py
git commit -m "统一三上下文资产采集"
```

### 任务 6：计算三角色被动覆盖差异

**文件：**
- 新建：`app/services/coverage_comparison.py`
- 新建：`tests/test_coverage_comparison.py`
- 修改：`app/services/scan_pipeline.py`

**接口：**
- 产出：`CoverageComparisonService.compare(session: Session, run: ScanRun) -> CoverageComparisonSummary`
- 产出：`CoverageClassification = shared | user_only | admin_only | unexpectedly_anonymous | inconsistent | unknown`

- [ ] **步骤 1：写覆盖矩阵测试**

```python
def test_compare_classifies_admin_only_asset(db_session, three_context_run):
    add_asset(db_session, three_context_run.admin, "/admin/users", status=200)
    summary = CoverageComparisonService().compare(db_session, three_context_run.run)
    item = summary.differences[0]
    assert item.classification == "admin_only"
    assert item.anonymous_present is False
    assert item.user_present is False
    assert item.admin_present is True
```

- [ ] **步骤 2：写 unknown 不等于 absent 测试**

```python
def test_failed_user_context_produces_unknown_not_difference(db_session, three_context_run):
    three_context_run.user.status = "failed"
    add_asset(db_session, three_context_run.admin, "/admin/users", status=200)
    item = CoverageComparisonService().compare(db_session, three_context_run.run).differences[0]
    assert item.user_state == "unknown"
    assert item.classification == "unknown"
```

- [ ] **步骤 3：写响应属性差异测试**

```python
def test_shared_asset_with_different_signature_is_inconsistent(db_session, three_context_run):
    add_same_asset_for_all(db_session, three_context_run, signatures=("a", "b", "c"))
    item = CoverageComparisonService().compare(db_session, three_context_run.run).differences[0]
    assert item.classification == "inconsistent"
```

- [ ] **步骤 4：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_coverage_comparison.py -v`

预期：比较服务不存在。

- [ ] **步骤 5：实现集合连接和分类规则**

以 `identity_key` 连接三上下文资产。只有 context 状态为 completed/completed_with_warnings 时，缺失才记为 absent；否则记为 unknown。每次 compare 先删除该 run 的旧 difference，再在同一事务中重建，保证复测幂等。

- [ ] **步骤 6：接入 `compare_coverage` 阶段并运行测试**

运行：`.venv/bin/pytest tests/test_coverage_comparison.py tests/test_assessment_application.py tests/test_url_scan_pipeline.py -v`

预期：authenticated-coverage 生成矩阵；quick 将该阶段标为 skipped。

- [ ] **步骤 7：提交**

```bash
git add app/services/coverage_comparison.py app/services/scan_pipeline.py tests/test_coverage_comparison.py
git commit -m "实现三角色覆盖差异计算"
```

### 任务 7：建立受保护重放载荷和候选策略

**文件：**
- 新建：`app/services/protected_payload_storage.py`
- 新建：`app/services/replay_policy.py`
- 新建：`tests/test_replay_policy.py`
- 修改：`app/core/settings.py:9-71`
- 修改：`app/services/context_collection.py`

**接口：**
- 产出：`ProtectedPayloadStorage.write(run_id: int, request_id: int, payload: dict[str, object]) -> str`
- 产出：`ProtectedPayloadStorage.read(storage_ref: str) -> dict[str, object]`
- 产出：`ProtectedPayloadStorage.delete(storage_ref: str) -> None`
- 产出：`ProtectedPayloadStorage.purge_expired(before: datetime) -> tuple[str, ...]`
- 产出：`ReplayPolicy.candidates(run: ScanRun) -> list[ReplayCandidate]`
- 产出：`ReplayCandidate`, `ReplayPolicyDecision`

- [ ] **步骤 1：写文件权限和原子写入测试**

```python
def test_protected_payload_is_owner_only(tmp_path):
    storage = ProtectedPayloadStorage(tmp_path)
    ref = storage.write(3, 7, {"url": "http://app/admin?id=1"})
    assert stat.S_IMODE(Path(ref).stat().st_mode) == 0o600
    assert not list(tmp_path.rglob("*.tmp"))
```

- [ ] **步骤 2：写显式删除和到期清理测试**

```python
def test_protected_payload_can_be_deleted_and_expired_payloads_are_purged(tmp_path, frozen_time):
    storage = ProtectedPayloadStorage(tmp_path)
    deleted_ref = storage.write(3, 7, {"url": "http://app/account"})
    expired_ref = storage.write(3, 8, {"url": "http://app/admin"})
    storage.delete(deleted_ref)
    set_mtime(expired_ref, frozen_time.minus(hours=25))
    purged = storage.purge_expired(frozen_time.minus(hours=24))
    assert not Path(deleted_ref).exists()
    assert purged == (expired_ref,)
    assert not Path(expired_ref).exists()
```

- [ ] **步骤 3：写允许方向和方法测试**

```python
def test_policy_only_selects_approved_downward_gets(run_with_requests):
    candidates = ReplayPolicy(max_candidates=50).candidates(run_with_requests)
    assert {(c.source_kind, c.target_kind) for c in candidates} == {
        ("user", "anonymous"),
        ("admin", "user"),
        ("admin", "anonymous"),
    }
    assert all(c.method in {"GET", "HEAD", "OPTIONS"} for c in candidates)
```

- [ ] **步骤 4：写危险请求拒绝测试**

```python
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_policy_rejects_state_changing_methods(method, request_factory):
    request = request_factory(method=method, body=b"x")
    decision = ReplayPolicy().evaluate(request)
    assert decision.allowed is False
    assert decision.reason == "method_not_allowed"
```

- [ ] **步骤 5：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_replay_policy.py -v`

预期：storage 和 policy 不存在。

- [ ] **步骤 6：实现 storage、预算、保留期和同源策略**

新增 settings：`GARDEN_PROTECTED_PAYLOAD_DIR`、`GARDEN_PROTECTED_PAYLOAD_RETENTION_HOURS=24`、`GARDEN_REPLAY_MAX_CANDIDATES=50`、`GARDEN_REPLAY_MAX_CONCURRENCY=2`、`GARDEN_REPLAY_TIMEOUT_SECONDS=5`、`GARDEN_REPLAY_MAX_RESPONSE_BYTES=1048576`。

策略类型固定为：

```python
@dataclass(frozen=True)
class ReplayCandidate:
    source_request_id: int
    source_context_id: int
    source_kind: str
    target_context_id: int
    target_kind: str
    method: str
    protected_storage_ref: str


@dataclass(frozen=True)
class ReplayPolicyDecision:
    allowed: bool
    reason: str
```

storage 使用 `os.open(..., 0o600)` 写临时文件、`fsync`、`os.replace` 原子替换；读取、删除和清理前都校验路径仍位于配置根目录。run 删除时同步删除其载荷，worker 启动时按保留期调用 `purge_expired()`；清理失败写脱敏审计事件但不泄漏路径内容。

- [ ] **步骤 7：运行安全回归**

运行：`.venv/bin/pytest tests/test_replay_policy.py tests/test_guardrails.py tests/test_context_collection.py -v`

预期：全部通过。

- [ ] **步骤 8：提交**

```bash
git add app/services/protected_payload_storage.py app/services/replay_policy.py app/core/settings.py app/services/context_collection.py tests/test_replay_policy.py
git commit -m "建立主动重放安全策略"
```

### 任务 8：实现跨角色重放网关和判定

**文件：**
- 新建：`app/integrations/replay/__init__.py`
- 新建：`app/integrations/replay/base.py`
- 新建：`app/integrations/replay/http_gateway.py`
- 新建：`app/integrations/replay/playwright_gateway.py`
- 新建：`app/services/replay_sessions.py`
- 新建：`app/services/authorization_replay.py`
- 新建：`tests/test_authorization_replay.py`

**接口：**
- 产出：`ReplayGateway.execute(candidate: ReplayCandidate, material: ReplaySessionMaterial) -> ReplayResponse`
- 产出：`ReplaySessionService.materialize(session: Session, context: ScanContext) -> ReplaySessionMaterial`
- 产出：`AuthorizationReplayService.execute(session: Session, run: ScanRun) -> ReplaySummary`
- 产出：`classify_replay(source: ResponseFingerprint, replay: ReplayResponse) -> ReplayVerdict`

- [ ] **步骤 1：写来源凭证剥离测试**

```python
def test_replay_replaces_source_authentication(fake_http_transport, replay_candidate):
    gateway = HttpReplayGateway(transport=fake_http_transport)
    material = ReplaySessionMaterial(cookies={"user_session": "low"}, headers={})
    gateway.execute(replay_candidate, material)
    sent = fake_http_transport.last_request
    assert "admin_session" not in sent.headers.get("cookie", "")
    assert "user_session=low" in sent.headers.get("cookie", "")
    assert sent.headers.get("authorization") is None
```

- [ ] **步骤 2：写四种判定测试**

```python
@pytest.mark.parametrize(
    ("status", "login_redirect", "similarity", "expected"),
    [
        (403, False, 0.0, "blocked"),
        (302, True, 0.0, "blocked"),
        (200, False, 0.95, "equivalent_access"),
        (200, False, 0.35, "changed_response"),
    ],
)
def test_classify_replay(status, login_redirect, similarity, expected):
    assert (
        classify_replay(make_source(), make_replay(status, login_redirect, similarity)) == expected
    )
```

- [ ] **步骤 3：写超时、跨源重定向和会话失效测试**

```python
def test_cross_origin_redirect_is_inconclusive(replay_service, cross_origin_gateway):
    result = replay_service.execute_one(candidate, gateway=cross_origin_gateway)
    assert result.verdict == "inconclusive"
    assert result.error_code == "redirect_target_blocked"
```

- [ ] **步骤 4：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_authorization_replay.py -v`

预期：网关、session materializer 和 replay service 不存在。

- [ ] **步骤 5：实现 HTTP 与 Playwright 网关**

HTTP gateway 使用 `httpx.Client(follow_redirects=False, trust_env=False)`，逐跳调用现有 `TargetNetworkPolicy`。Playwright gateway 使用目标 context 的 storage state 创建隔离 browser context；每个候选结束后关闭 context。

跨网关类型固定为：

```python
@dataclass(frozen=True)
class ReplaySessionMaterial:
    session_type: str
    cookies: dict[str, str]
    headers: dict[str, str]
    storage_state: dict[str, object] | None


@dataclass(frozen=True)
class ReplayResponse:
    status_code: int
    final_url: str
    redirects: tuple[str, ...]
    headers: dict[str, str]
    body_text: str
    elapsed_ms: int


@dataclass(frozen=True)
class ResponseFingerprint:
    status_code: int
    final_url: str
    content_signature: str
    login_redirect: bool
```

- [ ] **步骤 6：实现判定和幂等持久化**

`AuthorizationReplayService.execute()` 先删除该 run 上 status 为 pending 的旧执行，不覆盖已完成审计记录；重复运行通过 `(scan_run_id, source_request_id, target_context_id, policy_hash)` 唯一键避免重复。

- [ ] **步骤 7：运行重放和网络安全回归**

运行：`.venv/bin/pytest tests/test_authorization_replay.py tests/test_guardrails.py tests/test_http_auth.py tests/test_playwright_contract.py -v`

预期：全部通过，三种方向均有覆盖。

- [ ] **步骤 8：提交**

```bash
git add app/integrations/replay app/services/replay_sessions.py app/services/authorization_replay.py tests/test_authorization_replay.py
git commit -m "实现跨角色主动权限重放"
```

### 任务 9：接入 Evidence、Finding、审计和统一报告

**文件：**
- 修改：`app/models/scan_run.py`
- 修改：`app/models/audit_event.py`
- 修改：`app/models/enums.py`
- 修改：`app/services/authorization_replay.py`
- 修改：`app/services/scan_analysis.py`
- 修改：`app/services/scan_reporting.py`
- 修改：`tests/test_authorization_replay.py`
- 修改：`tests/test_url_scan_pipeline.py`
- 修改：`tests/test_findings_service.py`

**接口：**
- 产出：`ReplayFindingFactory.from_execution(execution: ReplayExecution) -> ScanFinding | None`
- 产出：`ScanReportService.generate(session: Session, scan_run_id: int) -> Path`

- [ ] **步骤 1：写 equivalent access finding 测试**

```python
def test_equivalent_access_creates_suspected_finding(db_session, equivalent_execution):
    finding = ReplayFindingFactory().from_execution(equivalent_execution)
    assert finding.category == "suspected_authorization_bypass"
    assert finding.confidence == "medium"
    assert finding.status == "new"
    assert finding.replay_execution_id == equivalent_execution.id
```

- [ ] **步骤 2：写 blocked/changed/inconclusive 不误报测试**

```python
@pytest.mark.parametrize("verdict", ["blocked", "changed_response", "inconclusive"])
def test_non_equivalent_verdict_does_not_create_vulnerability_finding(verdict):
    assert ReplayFindingFactory().from_execution(make_execution(verdict)) is None
```

- [ ] **步骤 3：写报告必需章节测试**

```python
def test_authenticated_report_contains_coverage_and_replay_sections(report_text):
    for heading in [
        "三角色会话有效性",
        "覆盖矩阵",
        "主动权限重放",
        "疑似授权缺陷",
        "完整性与未覆盖原因",
    ]:
        assert f"## {heading}" in report_text
```

- [ ] **步骤 4：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_authorization_replay.py tests/test_url_scan_pipeline.py -v`

预期：finding link/status 和报告章节不存在。

- [ ] **步骤 5：实现统一 evidence/finding 链接和审计事件**

新增 `REPLAY_CANDIDATE_SELECTED`、`REPLAY_EXECUTED`、`REPLAY_SKIPPED`、`REPLAY_FINDING_CREATED`。审计 payload 仅包含 run/context/request fingerprint、判定和跳过原因。

- [ ] **步骤 6：实现报告中的事实/推断/人工结论分层**

报告将 CoverageDifference 标为“观察事实”，自动 replay verdict 标为“自动推断”，只有 finding triage 状态为 accepted 才标为“人工确认”。

- [ ] **步骤 7：运行生命周期回归**

运行：`.venv/bin/pytest tests/test_authorization_replay.py tests/test_url_scan_pipeline.py tests/test_findings_service.py tests/test_workflow_lifecycle.py -v`

预期：全部通过。

- [ ] **步骤 8：提交**

```bash
git add app/models app/services/authorization_replay.py app/services/scan_analysis.py app/services/scan_reporting.py tests
git commit -m "统一重放证据与授权发现"
```

### 任务 10：增加规范 API、CLI 和操作者权限门槛

**文件：**
- 新建：`app/security/operator_auth.py`
- 新建：`app/api/routes/operator_auth.py`
- 新建：`app/api/routes/assessments.py`
- 新建：`app/cli/assessments.py`
- 新建：`tests/test_operator_auth.py`
- 新建：`tests/test_assessment_api.py`
- 新建：`tests/test_assessment_cli.py`
- 修改：`app/core/settings.py`
- 修改：`app/api/routes/scans.py`
- 修改：`app/api/router.py`
- 修改：`app/cli/scan.py`
- 修改：`app/cli/main.py`

**接口：**
- 产出：`require_operator(request: Request) -> OperatorPrincipal`
- 产出：`POST /api/assessments`
- 产出：`GET /api/assessments/{id}/contexts|differences|replays|report`
- 产出：`gardenctl assess start`

- [ ] **步骤 1：写未认证 API 禁止 active replay 测试**

```python
def test_active_replay_api_requires_operator(client, assessment_payload):
    assessment_payload["active_checks_enabled"] = True
    assessment_payload["authorization_confirmed"] = True
    response = client.post("/api/assessments", json=assessment_payload)
    assert response.status_code == 401
```

- [ ] **步骤 2：写签名 cookie 和 bearer token 测试**

```python
def test_operator_can_start_active_assessment(operator_client, assessment_payload):
    response = operator_client.post("/api/assessments", json=assessment_payload)
    assert response.status_code == 202
    assert response.json()["active_checks_enabled"] is True
```

- [ ] **步骤 3：写 CLI 双确认测试**

```python
def test_cli_rejects_active_replay_without_confirmation(runner):
    result = runner.invoke(
        app,
        [
            "assess",
            "start",
            "--url",
            LOCAL_URL,
            "--mode",
            "authenticated-coverage",
            "--user-profile",
            "user",
            "--admin-profile",
            "admin",
            "--enable-active-replay",
        ],
    )
    assert result.exit_code == 2
    assert "--confirm-authorized" in result.stdout
```

- [ ] **步骤 4：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_operator_auth.py tests/test_assessment_api.py tests/test_assessment_cli.py -v`

预期：新路由、命令和权限 dependency 不存在。

- [ ] **步骤 5：实现最小操作者认证**

settings 增加 `GARDEN_OPERATOR_TOKEN` 和 `GARDEN_OPERATOR_SESSION_SECRET`。`operator_auth.py` 使用标准库 `hmac.compare_digest` 校验 token，并用 HMAC-SHA256 签发带过期时间的 HttpOnly、SameSite=Strict cookie；production 环境强制 Secure。

- [ ] **步骤 6：实现 API/CLI 与 quick 兼容**

`POST /api/scans` 继续接受 `ScanStartRequest` 并内部构造 quick `AssessmentStartRequest`。`gardenctl scan` 继续输出原字段；新 `assess` 命令输出 mode、completeness 和 context counts。

- [ ] **步骤 7：运行入口回归**

运行：`.venv/bin/pytest tests/test_operator_auth.py tests/test_assessment_api.py tests/test_assessment_cli.py tests/test_api.py tests/test_quick_scan_cli.py tests/test_cli.py -v`

预期：全部通过。

- [ ] **步骤 8：提交**

```bash
git add app/security app/api app/cli app/core/settings.py tests/test_operator_auth.py tests/test_assessment_api.py tests/test_assessment_cli.py tests/test_api.py tests/test_quick_scan_cli.py
git commit -m "增加验证运行入口与主动重放权限"
```

### 任务 11：实现 Web 双入口、覆盖矩阵和重放结果页

**文件：**
- 新建：`app/templates/assessment_detail.html`
- 新建：`app/templates/operator_login.html`
- 修改：`app/templates/index.html:5-43`
- 修改：`app/templates/scan_detail.html`
- 修改：`app/templates/base.html`
- 修改：`app/api/routes/assessments.py`
- 修改：`app/api/routes/pages.py`
- 修改：`tests/test_dashboard.py`
- 修改：`tests/test_assessment_api.py`

**接口：**
- 产出：`GET /assessments/{id}`
- 产出：`POST /assessments`
- 保留：`GET /scans/{id}` 重定向或渲染 quick assessment。

- [ ] **步骤 1：写首页双入口渲染测试**

```python
def test_home_renders_quick_and_authenticated_entries(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Quick scan" in response.text
    assert "Authenticated coverage" in response.text
    assert 'name="user_profile_id"' in response.text
    assert 'name="admin_profile_id"' in response.text
```

- [ ] **步骤 2：写详情覆盖矩阵测试**

```python
def test_assessment_page_renders_context_matrix_and_replays(client, completed_assessment):
    response = client.get(f"/assessments/{completed_assessment.id}")
    assert response.status_code == 200
    for label in ["Anonymous", "User", "Admin", "Coverage differences", "Replay results"]:
        assert label in response.text
```

- [ ] **步骤 3：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_dashboard.py tests/test_assessment_api.py -v`

预期：首页仍只有单 URL form，assessment template 不存在。

- [ ] **步骤 4：实现轻量服务端渲染 UI**

首页 authenticated form 在未登录操作者时仍可创建被动三上下文运行，但 active checkbox disabled 并显示登录提示；登录后才允许勾选。详情页按 context、difference、replay、finding、failure 分区，不在模板中解析 raw payload。

- [ ] **步骤 5：运行 Web 回归**

运行：`.venv/bin/pytest tests/test_dashboard.py tests/test_assessment_api.py tests/test_api.py -v`

预期：全部通过。

- [ ] **步骤 6：提交**

```bash
git add app/templates app/api/routes/assessments.py app/api/routes/pages.py tests/test_dashboard.py tests/test_assessment_api.py
git commit -m "增加认证覆盖差异界面"
```

### 任务 12：实现可恢复 worker 和陈旧运行处理

**文件：**
- 新建：`app/workers/assessment_worker.py`
- 新建：`tests/test_assessment_worker.py`
- 修改：`app/services/scan_application.py`
- 修改：`app/main.py:17-29`
- 修改：`app/models/scan_run.py`
- 修改：`app/core/settings.py`

**接口：**
- 产出：`AssessmentWorker.claim_next() -> int | None`
- 产出：`AssessmentWorker.recover_interrupted(now: datetime) -> RecoverySummary`
- 产出：`DatabaseScanDispatcher.submit(scan_run_id: int, callback) -> None`

- [ ] **步骤 1：写重启恢复测试**

```python
def test_worker_requeues_expired_running_assessment(db_session, frozen_time):
    run = make_run(db_session, status="running", lease_expires_at=frozen_time.minus(minutes=5))
    summary = AssessmentWorker().recover_interrupted(frozen_time.now)
    db_session.refresh(run)
    assert summary.requeued_ids == [run.id]
    assert run.status == "queued"
    assert run.active_key is not None
```

- [ ] **步骤 2：写超过恢复次数进入 interrupted 测试**

```python
def test_worker_marks_repeated_crash_interrupted(db_session, frozen_time):
    run = make_run(
        db_session,
        status="running",
        recovery_count=3,
        lease_expires_at=frozen_time.minus(minutes=5),
    )
    AssessmentWorker(max_recoveries=3).recover_interrupted(frozen_time.now)
    db_session.refresh(run)
    assert run.status == "interrupted"
    assert run.active_key is None
```

- [ ] **步骤 3：写原子 claim 测试**

```python
def test_only_one_worker_claims_run(database_url):
    first = AssessmentWorker(database_url).claim_next()
    second = AssessmentWorker(database_url).claim_next()
    assert first is not None
    assert second is None
```

- [ ] **步骤 4：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_assessment_worker.py -v`

预期：worker 和 lease 字段不存在。

- [ ] **步骤 5：实现数据库租约 worker**

`ScanRun` 增加 `lease_owner`、`lease_expires_at`、`heartbeat_at`、`recovery_count`。SQLite 使用短事务和条件 UPDATE claim；PostgreSQL 路径使用 `FOR UPDATE SKIP LOCKED`。worker 每个阶段刷新 heartbeat，启动时先恢复过期运行。

恢复结果类型固定为：

```python
@dataclass(frozen=True)
class RecoverySummary:
    requeued_ids: tuple[int, ...]
    interrupted_ids: tuple[int, ...]
```

- [ ] **步骤 6：接入 lifespan 并保持 Inline dispatcher**

Web/API 默认使用 `DatabaseScanDispatcher`；CLI 测试和同步 quick 命令保留 `InlineScanDispatcher`。shutdown 最多等待当前阶段完成，不无限等待整个队列。

- [ ] **步骤 7：运行 worker 和 pipeline 回归**

运行：`.venv/bin/pytest tests/test_assessment_worker.py tests/test_assessment_application.py tests/test_url_scan_pipeline.py -v`

预期：全部通过。

- [ ] **步骤 8：提交**

```bash
git add app/workers/assessment_worker.py app/services/scan_application.py app/main.py app/models/scan_run.py app/core/settings.py tests/test_assessment_worker.py
git commit -m "实现验证运行重启恢复"
```

### 任务 13：迁移旧数据并切换兼容读取

**文件：**
- 新建：`migrations/versions/0003_migrate_legacy_workflow.py`
- 新建：`tests/test_legacy_assessment_migration.py`
- 修改：`app/services/inventory.py`
- 修改：`app/services/evidence.py`
- 修改：`app/services/findings.py`
- 修改：`app/services/reporting.py`
- 修改：`app/api/routes/jobs.py`
- 修改：`docs/legacy-cli-migration.md`

**接口：**
- 产出：`LegacyWorkflowReader`，只用于一个兼容窗口读取未迁移记录。
- 产出：旧 job/inventory/evidence/finding 到统一 run/context/asset/evidence/finding 的确定性映射。

- [ ] **步骤 1：写历史数据迁移保持计数测试**

```python
def test_legacy_migration_preserves_inventory_evidence_and_findings(legacy_phase7_db):
    before = legacy_counts(legacy_phase7_db)
    upgrade_database(legacy_phase7_db, "0003")
    after = unified_counts(legacy_phase7_db)
    assert after.assets == before.pages + before.endpoints + before.parameters
    assert after.evidence == before.evidence
    assert after.findings == before.findings
```

- [ ] **步骤 2：写旧数据不得伪装三角色完整测试**

```python
def test_migrated_legacy_run_is_marked_incomplete(legacy_phase7_db):
    upgrade_database(legacy_phase7_db, "0003")
    run = load_migrated_run(legacy_phase7_db)
    assert run.mode == "authenticated_coverage"
    assert run.completeness == "legacy_single_context"
    assert run.status == "incomplete"
```

- [ ] **步骤 3：运行测试并确认失败**

运行：`.venv/bin/pytest tests/test_legacy_assessment_migration.py -v`

预期：`0003` 和兼容 reader 不存在。

- [ ] **步骤 4：实现可重复数据迁移**

以 legacy job ID 生成稳定 source key，重复执行不会新增重复 run。旧 credential role 映射到 user/admin context；无法确定角色时标记 `legacy_unknown_role`，不生成 difference/replay。

- [ ] **步骤 5：切换读取并保留旧 CLI 只读提示**

旧 `job list/show`、inventory/evidence/finding 页面优先读取统一记录；遇到未迁移记录时通过 `LegacyWorkflowReader` 读取并显示中文弃用提示，不再创建新的 legacy job。

- [ ] **步骤 6：运行旧工作流回归**

运行：`.venv/bin/pytest tests/test_legacy_assessment_migration.py tests/test_inventory_services.py tests/test_evidence_service.py tests/test_findings_service.py tests/test_workflow_lifecycle.py tests/test_cli.py -v`

预期：全部通过。

- [ ] **步骤 7：提交**

```bash
git add migrations/versions/0003_migrate_legacy_workflow.py app/services app/api/routes/jobs.py tests/test_legacy_assessment_migration.py docs/legacy-cli-migration.md
git commit -m "迁移旧认证验证数据"
```

### 任务 14：增加真实浏览器 E2E、demo gate 和完整中文文档

**文件：**
- 新建：`tests/test_assessment_browser_e2e.py`
- 修改：`app/api/router.py:17-28`
- 修改：`app/main.py`
- 修改：`app/core/settings.py`
- 修改：`.github/workflows/ci.yml`
- 修改：`Dockerfile`
- 修改：`README.md`
- 修改：`PLANS.md`
- 修改：`docs/architecture.md`
- 修改：`docs/threat-model.md`
- 修改：`docs/deployment.md`
- 修改：`docs/demo-walkthrough.md`
- 修改：`docs/legacy-cli-migration.md`
- 修改：`app/models/README.md`
- 修改：`app/security_checks/README.md`
- 修改：`app/workers/README.md`

**接口：**
- 产出：CI `browser-e2e` job。
- 产出：`GARDEN_ENABLE_DEMO_ROUTES=false` 默认配置。
- 产出：非 root Docker runtime。

- [ ] **步骤 1：写 demo route gate 测试**

```python
def test_demo_routes_are_absent_outside_demo(monkeypatch):
    monkeypatch.setenv("GARDEN_ENABLE_DEMO_ROUTES", "false")
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/demo/auth/ui/login").status_code == 404
```

- [ ] **步骤 2：写真实 Chromium 三角色场景**

测试 fixture 必须提供：anonymous 可见 `/public`，user 可见 `/account`，admin 可见 `/admin/users`；普通用户请求 admin 资源返回 403；匿名重放一个故意配置错误的 user-only GET 返回 200，用于产生 `equivalent_access`。

```python
def test_real_browser_three_context_coverage_and_replay(live_demo, operator_client):
    run = start_real_assessment(operator_client, live_demo)
    result = wait_for_terminal(operator_client, run.id)
    assert result.status == "completed_with_warnings"
    assert result.context_counts == {"anonymous": 1, "user": 1, "admin": 1}
    assert any(item.classification == "admin_only" for item in result.differences)
    assert any(item.verdict == "equivalent_access" for item in result.replays)
```

- [ ] **步骤 3：运行本地浏览器测试并确认失败**

运行：`.venv/bin/playwright install chromium`

运行：`.venv/bin/pytest tests/test_assessment_browser_e2e.py -v`

预期：场景在 demo gate、统一 UI 或 replay 路径缺失处失败。

- [ ] **步骤 4：实现 demo gate、非 root 容器和 CI job**

CI job 安装 Chromium 后只运行 `tests/test_assessment_browser_e2e.py`；上传失败时的 Playwright trace 和脱敏截图。Dockerfile 创建固定 UID/GID 的 `garden` 用户，并确保 `/app/data`、`/app/exports`、protected payload 目录可写。

- [ ] **步骤 5：将产品和开发文档统一改为中文**

README、PLANS、架构、威胁模型、部署、演示、迁移和内部包 README 必须一致说明：认证验证是核心、quick scan 是入口、主动重放默认关闭且重点支持、三角色固定、旧 CLI 处于兼容窗口。

- [ ] **步骤 6：运行完整验证**

运行：`.venv/bin/ruff check .`

运行：`.venv/bin/ruff format --check .`

运行：`.venv/bin/pytest --cov=app --cov-report=term-missing:skip-covered`

运行：`.venv/bin/pytest tests/test_assessment_browser_e2e.py -v`

运行：`.venv/bin/alembic upgrade head`

运行：`docker build -t garden:authenticated-coverage .`

预期：全部退出码为 0；报告不包含 Cookie、Authorization、bearer token 或原始 session payload。

- [ ] **步骤 7：记录中文验收日志**

在 `docs/development/2026-08-11-authenticated-coverage-verification.md` 记录实际命令、退出码、测试数量、覆盖率、迁移结果、浏览器场景、Docker 构建结果和未完成项；没有运行的验证明确写“未验证”，不得推断通过。

- [ ] **步骤 8：提交**

```bash
git add .github/workflows/ci.yml Dockerfile app tests README.md PLANS.md docs
git commit -m "完成认证覆盖平台验收与中文文档"
```

### 任务 15：兼容窗口验收后移除旧工作流模型

> **执行门禁：** 任务 13、14 已在目标环境发布并完成一次兼容窗口验收，迁移前后计数对账为零差异，且用户明确批准退出兼容窗口后才能执行本任务。未满足门禁时在验收日志记录“等待兼容窗口退出批准”，不得删除旧表或旧入口。

**文件：**
- 新建：`migrations/versions/0004_drop_legacy_workflow.py`
- 新建：`tests/test_legacy_removal.py`
- 删除：`app/models/scan_job.py`
- 删除：`app/models/inventory_run.py`
- 删除：`app/models/inventory_page.py`
- 删除：`app/models/inventory_endpoint.py`
- 删除：`app/models/inventory_parameter.py`
- 删除：`app/models/inventory_annotation.py`
- 删除：`app/models/finding_retest.py`
- 删除：`app/services/jobs.py`
- 删除：`app/services/inventory.py`
- 删除：`app/services/inventory_collection.py`
- 删除：`app/services/inventory_annotations.py`
- 删除：`app/api/routes/jobs.py`
- 删除：`app/api/routes/inventory.py`
- 删除：`app/cli/jobs.py`
- 删除：`app/cli/inventory.py`
- 修改：`app/models/__init__.py`
- 修改：`app/api/router.py`
- 修改：`app/cli/main.py`
- 修改：`app/services/evidence.py`
- 修改：`app/services/findings.py`
- 修改：`app/services/reporting.py`
- 修改：`tests/test_migrations.py`
- 修改：`docs/legacy-cli-migration.md`
- 修改：`docs/development/2026-08-11-authenticated-coverage-verification.md`

**接口：**
- 产出：迁移 `0004_drop_legacy_workflow`，只删除已完成对账的旧 job/inventory/retest 表。
- 移除：`LegacyWorkflowReader`、旧 job/inventory 写入与读取入口。
- 保留：旧 quick `POST /api/scans` 和 `gardenctl scan` 兼容入口；它们已经映射到统一 assessment，不属于旧工作流模型。

- [ ] **步骤 1：写退出门禁失败测试**

```python
def test_legacy_drop_refuses_unreconciled_database(legacy_phase7_db):
    upgrade_database(legacy_phase7_db, "0003")
    mark_reconciliation(legacy_phase7_db, status="mismatch")
    with pytest.raises(LegacyRemovalBlocked, match="reconciliation_not_approved"):
        approve_legacy_removal(legacy_phase7_db)
```

- [ ] **步骤 2：写旧表移除和统一数据保留测试**

```python
def test_legacy_drop_removes_old_tables_and_preserves_unified_counts(reconciled_legacy_db):
    before = unified_counts(reconciled_legacy_db)
    upgrade_database(reconciled_legacy_db, "0004")
    assert not legacy_table_names() & inspect_table_names(reconciled_legacy_db)
    assert unified_counts(reconciled_legacy_db) == before
```

- [ ] **步骤 3：写旧入口移除测试**

```python
def test_removed_legacy_routes_are_not_registered(operator_client):
    assert operator_client.get("/jobs").status_code == 404
    assert operator_client.get("/inventory").status_code == 404
```

- [ ] **步骤 4：运行定向测试并确认失败**

运行：`.venv/bin/pytest tests/test_legacy_removal.py tests/test_migrations.py -v`

预期：`0004`、退出门禁和旧路径移除尚未实现。

- [ ] **步骤 5：实现退出门禁和删除迁移**

`approve_legacy_removal()` 必须校验：迁移状态为 `0003`、旧记录全部存在稳定映射、inventory/evidence/finding 计数一致、没有未迁移记录、批准者和批准时间已写入审计。`0004` 在删除表前重复执行相同断言，任一条件不满足即事务回滚。

- [ ] **步骤 6：移除旧代码并收紧兼容面**

删除旧 job/inventory 模型、service、API 和 CLI 注册；evidence、finding、report 仅接受统一 run/context 主键。对仍调用旧命令的用户返回 shell 层面的未知命令，不保留会重新引入平行领域模型的代理实现。

- [ ] **步骤 7：运行完整回归和迁移演练**

运行：`.venv/bin/pytest tests/test_legacy_removal.py tests/test_migrations.py tests/test_assessment_api.py tests/test_assessment_cli.py tests/test_assessment_browser_e2e.py -v`

运行：`.venv/bin/pytest --cov=app --cov-report=term-missing:skip-covered`

运行：`.venv/bin/ruff check .`

运行：`.venv/bin/ruff format --check .`

预期：全部退出码为 0；fresh database 直接升级到 head、已迁移 legacy database 升级到 `0004` 均成功；quick 兼容入口和规范 assessment 入口继续工作。

- [ ] **步骤 8：更新中文验收日志并提交**

日志记录批准人、批准时间、对账结果、备份位置、迁移命令、退出码和回滚演练结果；旧表删除属于不可逆生产动作，实际环境执行前必须再做可恢复备份。

```bash
git add migrations/versions/0004_drop_legacy_workflow.py app tests/test_legacy_removal.py tests/test_migrations.py docs/legacy-cli-migration.md docs/development/2026-08-11-authenticated-coverage-verification.md
git commit -m "移除已完成迁移的旧工作流模型"
```

---

## 全计划最终复核

- [ ] `gardenctl scan --url URL` 和 `POST /api/scans` 仍能完成 quick run。
- [ ] `gardenctl assess start --mode authenticated-coverage` 创建固定三上下文。
- [ ] 未显式确认授权时，CLI/API/Web 均不能启用 active replay。
- [ ] active replay 只包含三个批准方向和三种安全方法。
- [ ] 来源 Cookie/Authorization 不会进入目标角色请求。
- [ ] 登录或采集失败产生 incomplete/unknown，不产生虚假差异。
- [ ] equivalent access 只创建疑似授权 finding，人工确认前不称为已确认漏洞。
- [ ] queued/running 运行在重启后恢复或进入 interrupted，不永久占用 active key。
- [ ] fresh、legacy、rollback 迁移测试通过。
- [ ] 兼容窗口未获明确退出批准时，任务 15 保持未执行；获批后旧表移除且统一数据计数不变。
- [ ] 真实 Chromium 三角色 E2E、全量 pytest、Ruff、格式、Docker build 通过。
- [ ] 所有设计文档、实施计划、开发日志、验收记录和提交信息使用中文。
