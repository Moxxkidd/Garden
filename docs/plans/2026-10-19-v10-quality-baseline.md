# v1.0 质量与稳定性收敛

基线：origin/main b59a0ac（v0.9 PR #39 已合并，四项 CI SUCCESS）。原路线图第 10 节定义六个质量维度，不新增扫描动作、认证方式或自动重放。

## 实施范围

1. 固定合成基准：真实本地浏览器发现与候选确认计数，固定归并样本误合并计数。分子、分母和语料版本同时输出；真实站点不适用召回率。
2. 六维验收清单复用现有行为测试，新增开发者命令生成 JSON 与可读 Markdown 报告；失败、跳过、缺项均不能标成通过。
3. CI 在现有全量 pytest 中一次性采集结果，产出质量报告，不叠加一轮全量。报告记录 SHA、工作区状态、环境、耗时、具体失败/缺项和基准指标。
4. 更新版本、使用说明和展示页，完成针对性验证与一次全量回归，保留实际未验证项。不自动打 tag 或发布软件包。

发布门禁覆盖发现、归并、可信度、数据完整性、持续更新及边界控制。跨站归并、身份串用、导出泄密、范围越界用例属于必过项。报告只证明指定合成语料和指定环境中的检查结果，不等同独立安全审计或真实站点覆盖率。

成本控制：复用既有站点/测试，只补基准计数与报告错误判定所需用例；不新增依赖、不改生产扫描流程、不进行无关重构。

## 实施与验证记录

- 独立工作区 `.worktrees/quality-v10`、分支 `codex/quality-v10`，未修改主工作区已有未提交内容。
- `scripts/quality_baseline.py` 提供固定门禁与 `--full` 两种运行模式，JSON/Markdown 报告来源于真实 JUnit 结果；CI 改为后者，同一轮全量生成报告及附件。
- `docs/quality/corpus-v1.json` 的 20 个必需节点覆盖六个维度，参数化展开后固定门禁为 25 个用例；路径与函数定义已核对。
- 新增两个固定语料基准、一个参数化报告判定测试（6 种情形）。报告拒绝失败、跳过、缺项、零分母和 pytest 异常退出。未更改产品扫描流程、范围、认证或数据库结构。
- 基准初次接入时误用了 FetchResult.url，已按现有 final_url 接口修正。其后真实浏览器记录显示自动 favicon 请求影响候选分母；固定合成站点改用 data favicon 排除浏览器自动图标时序，未从结果中静默过滤失败候选。报告正确将失败基准显示为 blocked/未测量，其他维度分别保留通过状态。
- 新增用例与版本检查命令：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_quality_corpus.py tests/test_quality_report.py tests/test_cli.py tests/test_v030_release.py -q -o addopts='' --junitxml=.verification/quality-new.xml -o junit_family=legacy --tb=short`。18 passed / 7.47s。实测发现 3/3、候选确认 3/3、误合并 0/20，仅适用于固定语料。
- 代码冻结后 Ruff check、format check（373 文件）、git diff --check 通过。完整回归只执行一次 `python scripts/quality_baseline.py --full --output .verification/quality-full`；结果另记于下。
- wheel/sdist 初次构建：298/506 个成员；280 个应用文件及 wheel 内迁移逐字节一致，sdist 包含质量脚本、清单与基准用例，不包含 `.verification` 工作产物。
- 新建 Python 3.12 虚拟环境进行 wheel 干净安装；首次 pip 被继承的 SOCKS 代理缺少支持依赖阻断，仅对安装进程排除代理后重试。下载耗时不计作产品性能指标。

### 全量发现与待验证修复

冻结后全量实际结果：**922 passed / 1 failed / 2 warnings / 765.94s**；门禁报告耗时 770.29s，六个维度及三个指标通过，但整体正确为 `blocked`。失败为既有 `tests/test_identity_finalization.py::test_response_finishing_during_final_identity_proof_is_not_left_uncertain[False]`，不是新增基准用例。失败数据库中根页面为 confirmed，延迟 `/asset` 的资产与证据均仍为 identity_uncertain。不得把这轮结果写成全量通过。

代码检查发现 `request_finished` 在读取响应 headers/body 之前移除 active_requests；这些 Playwright 调用会让出事件循环，存在等待集合已空但观察尚未入库的窗口。已准备最小修复：在响应处理的 finally 中再移除请求。现有实机回归增加可控 headers 回调让步；固定门禁加入该双参数用例，现在为 21 个节点 / 27 个展开用例。没有放宽身份确认断言，也没有修改请求范围、预算或重放行为。

**修复尚未经过真实浏览器复测。** 自动审批服务多次返回 Selected model is at capacity 或审批超时，针对性 pytest 命令未被执行。已请求用户明确批准继续本地验证；不把服务故障解释为操作不安全。新补丁之后仅执行无浏览器的报告/身份健康测试及静态检查，结果另记。本次构建包早于该补丁，不能作为最终交付包；恢复验证后须重新构建。

干净安装的 Playwright 1.64.0 wheel 下载很慢。计划使用已验证且满足依赖约束的 1.58.0 缓存版本完成新环境安装，但查询/调整安装进程同样遇到自动审批服务故障，尚未执行。安装状态不能算通过。Linux v1.0 CI、最终包安装冒烟与提交/推送均未完成，不创建 PR/tag/release。

补丁后的非浏览器验证：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_quality_report.py tests/test_identity_health.py -q -o addopts=''`：12 passed / 5.42s；Ruff check、373 文件 format check 和 git diff --check 通过。此结果不能替代延迟响应的真实浏览器回归。安装后台会话 20533 截至交接仍在下载，没有成功或失败终态；恢复时先核对进程，避免重复启动安装。

### PR 交付前恢复验证（2026-10-19）

用户授权创建 PR 后，自动审批恢复，远端 main 仍为 b59a0ac，无需整合。上述浏览器验证阻塞已解除：`PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_identity_finalization.py tests/test_quality_corpus.py tests/test_quality_report.py -q -o addopts='' --tb=short`：10 passed / 14.56s，包含带可控回调让步的身份确认及超时分支。

最终固定门禁命令：`/Users/an/Documents/Garden/.venv/bin/python scripts/quality_baseline.py --output .verification/quality-final`，报告 passed，21 个必需节点、27 个展开用例全部通过，报告总耗时 101.36s；发现 3/3、候选确认 3/3、误合并 0/20。之前 922 passed / 1 failed 的完整结果保留为发现缺陷的历史证据，不改写为成功；修复后的全量验证交给本 PR 的 Linux 双 Python CI，不额外重复本地全量。

上次后台干净安装已成功，实际使用 Python 3.12.14 / Playwright 1.64.0，无需降到缓存版本。重新构建最终 wheel 并在该新环境无依赖重装，280 个应用文件及迁移与源码逐字节一致。`PYTHONPATH= .verification/install-v100/bin/python .verification/v100_installed_smoke.py` 成功：site-packages 导入、真实 loopback 请求 1 次、扫描与项目索引、1 条 delivery-v1 观察、API/CLI 内容一致、Web/schema 成功、Garden 1.0.0。日志 `.verification/v100-installed-smoke.log`，安装验证未运行浏览器；浏览器行为由前述开发环境回归验证。

Ruff check、373 文件格式检查和 git diff --check 通过。CI 质量附件显式允许 `.verification/quality` 下的隐藏路径；文件均来自合成测试。用户现已授权提交、推送与创建 PR，仍不合并、不打 tag、不发布软件包。
